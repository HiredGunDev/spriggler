"""Running estimators for calibration experiments.

Continuously fit models to streaming data and report when the estimate
has converged (error bars are small enough to be useful) or when
the signal is too weak to characterize.

Each estimator maintains its own state and can accept new data points
one at a time. After each new point, it re-evaluates:
    - current estimate and error bars
    - whether the estimate has converged
    - whether the signal is detectable above noise

Three possible outcomes:
    CONVERGED:  Error bars are tight. The estimate is usable.
    NO_SIGNAL:  Ran long enough to conclude the effect is below noise.
    RUNNING:    Not enough data yet. Keep sampling.
"""

import math
from dataclasses import dataclass, field
from enum import Enum


class EstimateStatus(Enum):
    RUNNING = "running"
    CONVERGED = "converged"
    NO_SIGNAL = "no_signal"


@dataclass
class Estimate:
    """Current state of an estimator."""
    value: float = 0.0
    std_error: float = float('inf')
    r_squared: float = 0.0
    sample_count: int = 0
    status: EstimateStatus = EstimateStatus.RUNNING
    message: str = ""

    @property
    def relative_error(self) -> float:
        """Standard error as fraction of estimate. Inf if estimate is ~0."""
        if abs(self.value) < 1e-15:
            return float('inf')
        return self.std_error / abs(self.value)

    @property
    def confidence_low(self) -> float:
        """Lower bound of ~95% confidence interval (2 SE)."""
        return self.value - 2 * self.std_error

    @property
    def confidence_high(self) -> float:
        """Upper bound of ~95% confidence interval (2 SE)."""
        return self.value + 2 * self.std_error


class RateEstimator:
    """Estimates the rate of change of a property over time.

    Fits y = slope * t + intercept via least squares on streaming data.
    Reports slope (rate of change) with standard error.

    Use for: temperature rise rate from a heater, humidity change rate, etc.

    Convergence: relative error of slope < threshold.
    No signal: slope confidence interval includes zero after min_time.
    """

    def __init__(self,
                 convergence_threshold: float = 0.10,
                 min_samples: int = 10,
                 min_time_seconds: float = 120.0,
                 no_signal_time_seconds: float = 300.0):
        """
        Args:
            convergence_threshold: Stop when SE/|slope| < this (default 10%).
            min_samples: Minimum samples before declaring converged.
            min_time_seconds: Minimum time before declaring converged.
            no_signal_time_seconds: Time after which no signal is declared
                if confidence interval still includes zero.
        """
        self.convergence_threshold = convergence_threshold
        self.min_samples = min_samples
        self.min_time_seconds = min_time_seconds
        self.no_signal_time_seconds = no_signal_time_seconds

        # Running sums for incremental least squares
        self._n = 0
        self._sum_x = 0.0
        self._sum_y = 0.0
        self._sum_xy = 0.0
        self._sum_x2 = 0.0
        self._sum_y2 = 0.0
        self._t0: float | None = None
        self._last_t: float = 0.0

    def add(self, timestamp: float, value: float) -> Estimate:
        """Add a new data point and return current estimate.

        Args:
            timestamp: Unix timestamp of the reading.
            value: The property value (e.g., temperature in Kelvin).

        Returns:
            Current Estimate with status.
        """
        if self._t0 is None:
            self._t0 = timestamp

        t = timestamp - self._t0
        self._last_t = t

        self._n += 1
        self._sum_x += t
        self._sum_y += value
        self._sum_xy += t * value
        self._sum_x2 += t * t
        self._sum_y2 += value * value

        return self._evaluate()

    def _evaluate(self) -> Estimate:
        """Compute current estimate with error bars and status."""
        n = self._n

        if n < 3:
            return Estimate(
                sample_count=n,
                status=EstimateStatus.RUNNING,
                message=f"Need at least 3 samples (have {n})",
            )

        # Least squares: y = slope * x + intercept
        denom = n * self._sum_x2 - self._sum_x ** 2
        if abs(denom) < 1e-15:
            return Estimate(
                sample_count=n,
                status=EstimateStatus.RUNNING,
                message="Insufficient time spread",
            )

        slope = (n * self._sum_xy - self._sum_x * self._sum_y) / denom
        intercept = (self._sum_y - slope * self._sum_x) / n

        # R²
        y_mean = self._sum_y / n
        ss_tot = self._sum_y2 - n * y_mean * y_mean
        ss_res = (self._sum_y2
                  - 2 * slope * self._sum_xy
                  - 2 * intercept * self._sum_y
                  + slope * slope * self._sum_x2
                  + 2 * slope * intercept * self._sum_x
                  + n * intercept * intercept)

        r_squared = 1.0 - ss_res / ss_tot if abs(ss_tot) > 1e-15 else 0.0

        # Standard error of slope
        # SE(slope) = sqrt(MSE / Sxx) where Sxx = sum((xi - xbar)²)
        if n > 2:
            mse = max(ss_res / (n - 2), 0.0)
            sxx = self._sum_x2 - self._sum_x ** 2 / n
            if sxx > 1e-15:
                se_slope = math.sqrt(mse / sxx)
            else:
                se_slope = float('inf')
        else:
            se_slope = float('inf')

        elapsed = self._last_t

        est = Estimate(
            value=slope,
            std_error=se_slope,
            r_squared=r_squared,
            sample_count=n,
        )

        # Check convergence
        if n >= self.min_samples and elapsed >= self.min_time_seconds:
            if abs(slope) > 1e-15 and est.relative_error < self.convergence_threshold:
                est.status = EstimateStatus.CONVERGED
                est.message = (
                    f"Rate converged: {slope:.6f} ± {se_slope:.6f}/s "
                    f"(RE={est.relative_error:.1%})"
                )
                return est

        # Check for no signal
        if elapsed >= self.no_signal_time_seconds and n >= self.min_samples:
            # Confidence interval includes zero?
            if est.confidence_low <= 0 <= est.confidence_high:
                est.status = EstimateStatus.NO_SIGNAL
                est.message = (
                    f"No detectable signal after {elapsed:.0f}s. "
                    f"Rate: {slope:.6f} ± {se_slope:.6f}/s "
                    f"(CI includes zero)"
                )
                return est

        est.status = EstimateStatus.RUNNING
        est.message = (
            f"Rate: {slope:.6f} ± {se_slope:.6f}/s "
            f"(RE={est.relative_error:.1%}, {n} samples, {elapsed:.0f}s)"
        )
        return est


class DecayEstimator:
    """Estimates exponential decay time constant τ.

    Fits ln(differential) = -t/τ + b via linearized least squares.
    Reports τ with standard error.

    Use for: envelope conductance from post-heater decay,
             transfer device effective conductance.

    The data fed in should be (timestamp, differential) where
    differential = interior - ambient (or similar).

    Convergence: relative error of τ < threshold AND R² > min_r_squared.
    No signal: differential not decaying after min_time.
    """

    def __init__(self,
                 convergence_threshold: float = 0.10,
                 min_r_squared: float = 0.95,
                 min_samples: int = 15,
                 min_time_seconds: float = 180.0,
                 no_signal_time_seconds: float = 300.0,
                 min_differential: float = 1.0):
        """
        Args:
            convergence_threshold: Stop when SE(τ)/|τ| < this.
            min_r_squared: Minimum R² for convergence.
            min_samples: Minimum samples before declaring converged.
            min_time_seconds: Minimum time before declaring converged.
            no_signal_time_seconds: Time to wait before declaring no signal.
            min_differential: Ignore samples below this (log transform).
        """
        self.convergence_threshold = convergence_threshold
        self.min_r_squared = min_r_squared
        self.min_samples = min_samples
        self.min_time_seconds = min_time_seconds
        self.no_signal_time_seconds = no_signal_time_seconds
        self.min_differential = min_differential

        # We can't do fully incremental log-transformed LS easily
        # because we filter out small differentials. Store raw points.
        self._points: list[tuple[float, float]] = []  # (time_rel, diff)
        self._t0: float | None = None
        self._last_t: float = 0.0

    def add(self, timestamp: float, differential: float) -> Estimate:
        """Add a new data point and return current estimate.

        Args:
            timestamp: Unix timestamp.
            differential: Property differential (e.g., interior - ambient).

        Returns:
            Current Estimate of τ with status.
        """
        if self._t0 is None:
            self._t0 = timestamp

        t = timestamp - self._t0
        self._last_t = t
        self._points.append((t, differential))

        return self._evaluate()

    def _evaluate(self) -> Estimate:
        """Compute current estimate of τ."""
        # Filter to usable points (positive differential above threshold)
        usable = [
            (t, math.log(d))
            for t, d in self._points
            if d > self.min_differential
        ]

        n = len(usable)
        elapsed = self._last_t

        if n < 5:
            return Estimate(
                sample_count=len(self._points),
                status=EstimateStatus.RUNNING,
                message=f"Need 5+ samples above min_differential (have {n})",
            )

        # Least squares on log-transformed data: ln(d) = slope*t + intercept
        sum_x = sum(t for t, _ in usable)
        sum_y = sum(y for _, y in usable)
        sum_xy = sum(t * y for t, y in usable)
        sum_x2 = sum(t * t for t, _ in usable)
        sum_y2 = sum(y * y for _, y in usable)

        denom = n * sum_x2 - sum_x ** 2
        if abs(denom) < 1e-15:
            return Estimate(
                sample_count=len(self._points),
                status=EstimateStatus.RUNNING,
                message="Insufficient time spread",
            )

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # Not decaying?
        if slope >= 0:
            if elapsed >= self.no_signal_time_seconds:
                return Estimate(
                    sample_count=len(self._points),
                    status=EstimateStatus.NO_SIGNAL,
                    message=(
                        f"Differential not decaying after {elapsed:.0f}s "
                        f"(slope={slope:.6f})"
                    ),
                )
            return Estimate(
                sample_count=len(self._points),
                status=EstimateStatus.RUNNING,
                message=f"Waiting for decay (slope={slope:.6f})",
            )

        tau = -1.0 / slope
        conductance = 1.0 / tau

        # R²
        y_mean = sum_y / n
        ss_tot = sum_y2 - n * y_mean * y_mean
        ss_res = sum(
            (y - (slope * t + intercept)) ** 2
            for t, y in usable
        )
        r_squared = 1.0 - ss_res / ss_tot if abs(ss_tot) > 1e-15 else 0.0

        # Standard error of slope → SE of τ
        # SE(slope) from regression, then τ = -1/slope, so by delta method:
        # SE(τ) ≈ SE(slope) / slope²
        mse = max(ss_res / (n - 2), 0.0) if n > 2 else 0.0
        sxx = sum_x2 - sum_x ** 2 / n
        if sxx > 1e-15 and n > 2:
            se_slope = math.sqrt(mse / sxx)
            se_tau = se_slope / (slope * slope)
        else:
            se_slope = float('inf')
            se_tau = float('inf')

        est = Estimate(
            value=tau,
            std_error=se_tau,
            r_squared=r_squared,
            sample_count=len(self._points),
        )

        # Check convergence
        if (n >= self.min_samples
                and elapsed >= self.min_time_seconds
                and r_squared >= self.min_r_squared
                and est.relative_error < self.convergence_threshold):
            est.status = EstimateStatus.CONVERGED
            est.message = (
                f"τ converged: {tau:.1f}s ± {se_tau:.1f}s "
                f"(RE={est.relative_error:.1%}, R²={r_squared:.4f})"
            )
            return est

        # Check no signal (differential not decaying meaningfully)
        if elapsed >= self.no_signal_time_seconds and n >= self.min_samples:
            if r_squared < 0.5:
                est.status = EstimateStatus.NO_SIGNAL
                est.message = (
                    f"Poor decay fit after {elapsed:.0f}s "
                    f"(R²={r_squared:.4f}). Differential may not be "
                    f"decaying exponentially."
                )
                return est

        est.status = EstimateStatus.RUNNING
        est.message = (
            f"τ: {tau:.1f}s ± {se_tau:.1f}s "
            f"(RE={est.relative_error:.1%}, R²={r_squared:.4f}, "
            f"{n} usable/{len(self._points)} total, {elapsed:.0f}s)"
        )
        return est

    @property
    def tau(self) -> float | None:
        """Current τ estimate, or None if not enough data."""
        est = self._evaluate()
        return est.value if est.value > 0 else None

    @property
    def conductance(self) -> float | None:
        """Current conductance (1/τ), or None."""
        t = self.tau
        return 1.0 / t if t and t > 0 else None