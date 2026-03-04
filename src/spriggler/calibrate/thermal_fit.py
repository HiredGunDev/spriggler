"""Thermal curve fitting for calibration experiments.

Fits exponential decay and rise curves to extract envelope conductance
and device thermal contribution rates. Pure Python, no numpy/scipy.

The key insight: exponential decay is linearizable.

    T(t) = T_ambient + ΔT₀ × e^(-t/τ)

Take the differential:

    T(t) - T_ambient = ΔT₀ × e^(-t/τ)

Take the log:

    ln(T(t) - T_ambient) = ln(ΔT₀) - t/τ

This is a line: y = mx + b where:
    y = ln(differential)
    x = t (time)
    m = -1/τ (slope gives time constant)
    b = ln(ΔT₀) (intercept gives initial differential)

Linear least squares on the log-transformed data gives τ directly.
"""

import math
from dataclasses import dataclass, field


@dataclass
class ThermalSample:
    """A single timestamped temperature reading pair."""
    timestamp: float          # Unix timestamp
    interior: float           # Interior temperature (Kelvin)
    ambient: float            # Ambient temperature (Kelvin)

    @property
    def differential(self) -> float:
        """Temperature difference: interior - ambient."""
        return self.interior - self.ambient


@dataclass
class DecayFit:
    """Results of fitting an exponential decay curve."""
    tau: float                # Time constant (seconds)
    conductance: float        # Aggregate envelope conductance (1/τ)
    initial_differential: float  # ΔT₀ from fit (Kelvin)
    final_differential: float    # Last measured differential (Kelvin)
    r_squared: float          # Goodness of fit (0-1)
    tau_std_error: float      # SE(τ) in seconds
    tau_relative_error: float # SE(τ)/τ — fractional uncertainty
    duration_seconds: float   # Total decay observation time
    sample_count: int         # Number of data points used
    residuals_mean: float     # Mean absolute residual (Kelvin)


@dataclass
class RiseFit:
    """Results of fitting a device rise phase."""
    gross_rise_rate: float       # Observed dT/dt (K/s) including envelope loss
    net_rise_rate: float         # Device contribution only (K/s), envelope loss removed
    device_watts_thermal: float  # Estimated thermal watts (if power data available)
    power_watts_electrical: float  # Measured electrical watts during rise
    thermal_efficiency: float    # thermal/electrical ratio
    peak_differential: float     # Maximum differential reached (Kelvin)
    duration_seconds: float      # Total rise time
    sample_count: int


def fit_decay(samples: list[ThermalSample],
              min_differential: float = 1.0) -> DecayFit:
    """Fit an exponential decay curve to temperature differential data.

    Args:
        samples: Time-ordered thermal samples from the decay phase.
            Should start from peak differential (device just turned off).
        min_differential: Ignore samples where differential drops below
            this (Kelvin). Near-zero differentials blow up the log transform.

    Returns:
        DecayFit with time constant and derived conductance.

    Raises:
        ValueError: If insufficient data for a meaningful fit.
    """
    if len(samples) < 5:
        raise ValueError(
            f"Need at least 5 samples for decay fit, got {len(samples)}"
        )

    # Filter to samples with meaningful differential
    t0 = samples[0].timestamp
    points = []
    for s in samples:
        diff = s.differential
        if diff > min_differential:
            t = s.timestamp - t0
            points.append((t, diff))

    if len(points) < 5:
        raise ValueError(
            f"Only {len(points)} samples above min_differential "
            f"({min_differential}K). Need at least 5."
        )

    # Linearize: y = ln(diff), x = t
    # Fit y = mx + b via least squares
    xs = [t for t, _ in points]
    ys = [math.log(diff) for _, diff in points]

    slope, intercept, r_squared, slope_se = _linear_least_squares(xs, ys)

    # slope = -1/τ → τ = -1/slope
    if slope >= 0:
        raise ValueError(
            "Decay slope is non-negative — temperature is not decaying. "
            "Check that the device was actually turned off."
        )

    tau = -1.0 / slope
    initial_diff_fit = math.exp(intercept)
    conductance = 1.0 / tau

    # SE(τ) via delta method: τ = -1/slope → SE(τ) = SE(slope) / slope²
    if slope_se < float('inf') and abs(slope) > 1e-15:
        tau_se = slope_se / (slope ** 2)
        tau_re = tau_se / tau
    else:
        tau_se = float('inf')
        tau_re = float('inf')

    # Compute residuals against the fit
    residuals = []
    for t, diff in points:
        predicted = initial_diff_fit * math.exp(-t / tau)
        residuals.append(abs(diff - predicted))

    return DecayFit(
        tau=round(tau, 2),
        conductance=round(conductance, 6),
        initial_differential=round(initial_diff_fit, 3),
        final_differential=round(samples[-1].differential, 3),
        r_squared=round(r_squared, 4),
        tau_std_error=round(tau_se, 2),
        tau_relative_error=round(tau_re, 4),
        duration_seconds=round(samples[-1].timestamp - samples[0].timestamp, 1),
        sample_count=len(points),
        residuals_mean=round(sum(residuals) / len(residuals), 4),
    )


def fit_rise(samples: list[ThermalSample],
             envelope_conductance: float | None = None,
             power_watts: float | None = None) -> RiseFit:
    """Analyze a device rise phase to extract thermal contribution.

    Args:
        samples: Time-ordered thermal samples from the rise phase.
            Should start from when device was turned on.
        envelope_conductance: If known (from decay fit), used to
            subtract envelope loss from gross rise rate to isolate
            the device's contribution. If None, only gross rate
            is computed.
        power_watts: Electrical power draw during rise (from KASA).
            Used to compute thermal efficiency.

    Returns:
        RiseFit with device contribution rates.

    Raises:
        ValueError: If insufficient data.
    """
    if len(samples) < 3:
        raise ValueError(
            f"Need at least 3 samples for rise analysis, got {len(samples)}"
        )

    t0 = samples[0].timestamp
    duration = samples[-1].timestamp - t0

    if duration < 1.0:
        raise ValueError(f"Rise duration too short: {duration:.1f}s")

    # Compute gross rise rate via linear fit on differential vs time
    xs = [s.timestamp - t0 for s in samples]
    ys = [s.differential for s in samples]

    slope, _, r_sq, _ = _linear_least_squares(xs, ys)
    gross_rise_rate = slope  # K/s

    # Net rise rate: subtract envelope loss
    # During rise, envelope is losing heat at rate = conductance × avg_differential
    # The device's true contribution = observed rise + envelope loss
    if envelope_conductance is not None:
        avg_differential = sum(ys) / len(ys)
        envelope_loss_rate = envelope_conductance * avg_differential
        net_rise_rate = gross_rise_rate + envelope_loss_rate
    else:
        net_rise_rate = gross_rise_rate
        envelope_loss_rate = 0.0

    # Thermal watts: requires knowing thermal mass of the space,
    # which we don't have directly. But we can report the rate
    # and let the solver use it directly.
    # If we have electrical power, report efficiency as a ratio
    # of rise rates (useful for relative comparison).
    device_watts_thermal = 0.0
    thermal_efficiency = 0.0
    if power_watts and power_watts > 0:
        # This is an approximation — true thermal watts requires
        # knowing the thermal mass. For now, record the ratio.
        thermal_efficiency = net_rise_rate / gross_rise_rate if gross_rise_rate > 0 else 0.0

    peak_diff = max(s.differential for s in samples)

    return RiseFit(
        gross_rise_rate=round(gross_rise_rate, 6),
        net_rise_rate=round(net_rise_rate, 6),
        device_watts_thermal=round(device_watts_thermal, 2),
        power_watts_electrical=round(power_watts, 2) if power_watts else 0.0,
        thermal_efficiency=round(thermal_efficiency, 4),
        peak_differential=round(peak_diff, 3),
        duration_seconds=round(duration, 1),
        sample_count=len(samples),
    )


def _linear_least_squares(xs: list[float],
                          ys: list[float]) -> tuple[float, float, float]:
    """Ordinary least squares fit: y = mx + b.

    Args:
        xs: Independent variable values.
        ys: Dependent variable values.

    Returns:
        (slope, intercept, r_squared, slope_std_error)
    """
    n = len(xs)
    if n < 2:
        raise ValueError("Need at least 2 points for linear fit")

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-15:
        raise ValueError("All x values are identical — cannot fit a line")

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R² — coefficient of determination
    y_mean = sum_y / n
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))

    if ss_tot < 1e-15:
        r_squared = 1.0
    else:
        r_squared = 1.0 - ss_res / ss_tot

    # Standard error of slope
    if n > 2:
        mse = ss_res / (n - 2)
        x_mean = sum_x / n
        sxx = sum((x - x_mean) ** 2 for x in xs)
        slope_se = math.sqrt(mse / sxx) if sxx > 1e-15 else float('inf')
    else:
        slope_se = float('inf')

    return slope, intercept, r_squared, slope_se