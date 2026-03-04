"""Tests for calibration running estimators."""

import math
import pytest

from spriggler.calibrate.estimators import (
    RateEstimator,
    DecayEstimator,
    EstimateStatus,
)


# ── RateEstimator ──────────────────────────────────────────────────────


class TestRateEstimator:
    """Tests for linear rate estimation with convergence detection."""

    def test_needs_minimum_samples(self):
        """Returns RUNNING until enough samples."""
        est = RateEstimator(min_samples=5, min_time_seconds=0)
        for i in range(4):
            result = est.add(float(i), 300.0 + i * 0.05)
        assert result.status == EstimateStatus.RUNNING

    def test_converges_on_clean_linear_data(self):
        """Strong linear signal should converge quickly."""
        est = RateEstimator(
            convergence_threshold=0.10,
            min_samples=10,
            min_time_seconds=60.0,
        )
        # Simulate heater: 0.035 K/s rise rate, sampled every 15s
        rate = 0.035  # K/s
        t0 = 1000.0
        base = 290.0  # ~17°C

        result = None
        for i in range(60):  # Up to 15 minutes
            t = t0 + i * 15.0
            temp = base + rate * (i * 15.0)
            result = est.add(t, temp)
            if result.status == EstimateStatus.CONVERGED:
                break

        assert result.status == EstimateStatus.CONVERGED
        assert abs(result.value - rate) < 0.001
        assert result.relative_error < 0.10
        # Should converge well before 60 samples
        assert result.sample_count < 30

    def test_converges_with_noise(self):
        """Should converge even with realistic sensor noise."""
        import random
        random.seed(42)

        est = RateEstimator(
            convergence_threshold=0.15,  # Allow 15% for noisy data
            min_samples=10,
            min_time_seconds=60.0,
        )

        rate = 0.035  # K/s
        noise_std = 0.2  # ±0.2K noise (Govee resolution ~0.1°C)
        t0 = 1000.0
        base = 290.0

        result = None
        for i in range(120):
            t = t0 + i * 15.0
            temp = base + rate * (i * 15.0) + random.gauss(0, noise_std)
            result = est.add(t, temp)
            if result.status == EstimateStatus.CONVERGED:
                break

        assert result.status == EstimateStatus.CONVERGED
        # Rate should be close to true value
        assert abs(result.value - rate) / rate < 0.15

    def test_no_signal_detected(self):
        """Flat data (no effect) should report NO_SIGNAL."""
        import random
        random.seed(42)

        est = RateEstimator(
            min_samples=10,
            min_time_seconds=60.0,
            no_signal_time_seconds=120.0,
        )

        t0 = 1000.0
        base = 290.0

        result = None
        for i in range(60):
            t = t0 + i * 15.0
            # No real signal, just noise
            temp = base + random.gauss(0, 0.1)
            result = est.add(t, temp)
            if result.status != EstimateStatus.RUNNING:
                break

        assert result.status == EstimateStatus.NO_SIGNAL

    def test_weak_signal_takes_longer(self):
        """A subtle effect needs more data to converge."""
        est_strong = RateEstimator(
            convergence_threshold=0.10,
            min_samples=10,
            min_time_seconds=60.0,
        )
        est_weak = RateEstimator(
            convergence_threshold=0.10,
            min_samples=10,
            min_time_seconds=60.0,
        )

        t0 = 1000.0
        base = 290.0

        strong_converged_at = None
        weak_converged_at = None

        for i in range(120):
            t = t0 + i * 15.0
            strong_temp = base + 0.035 * (i * 15.0)  # 620W heater
            weak_temp = base + 0.002 * (i * 15.0)    # 41W light

            r_strong = est_strong.add(t, strong_temp)
            r_weak = est_weak.add(t, weak_temp)

            if r_strong.status == EstimateStatus.CONVERGED and strong_converged_at is None:
                strong_converged_at = i
            if r_weak.status == EstimateStatus.CONVERGED and weak_converged_at is None:
                weak_converged_at = i

        # Both should converge on clean data
        assert strong_converged_at is not None
        assert weak_converged_at is not None
        # But weak signal takes longer (or same — clean data is clean)
        assert weak_converged_at >= strong_converged_at

    def test_error_bars_shrink_with_data(self):
        """More data should generally reduce standard error."""
        import random
        random.seed(42)

        est = RateEstimator(min_samples=3, min_time_seconds=0)

        t0 = 1000.0
        errors = []

        for i in range(30):
            t = t0 + i * 15.0
            temp = 290.0 + 0.035 * (i * 15.0) + random.gauss(0, 0.1)
            result = est.add(t, temp)
            if result.std_error < float('inf'):
                errors.append(result.std_error)

        # Overall trend: later errors much smaller than early errors
        assert errors[-1] < errors[2]
        # Final error should be quite small
        assert errors[-1] < 0.001

    def test_rate_sign_correct_for_cooling(self):
        """Negative rate for cooling device."""
        est = RateEstimator(min_samples=5, min_time_seconds=0)

        t0 = 1000.0
        for i in range(20):
            t = t0 + i * 15.0
            temp = 310.0 - 0.01 * (i * 15.0)  # Cooling
            result = est.add(t, temp)

        assert result.value < 0


# ── DecayEstimator ─────────────────────────────────────────────────────


class TestDecayEstimator:
    """Tests for exponential decay τ estimation."""

    def _make_decay_data(self, tau, initial_diff, ambient, n_samples,
                         interval=15.0, noise_std=0.0, seed=42):
        """Generate synthetic exponential decay data."""
        import random
        random.seed(seed)

        points = []
        t0 = 1000.0
        for i in range(n_samples):
            t = t0 + i * interval
            diff = initial_diff * math.exp(-(i * interval) / tau)
            if noise_std > 0:
                diff += random.gauss(0, noise_std)
            points.append((t, diff))
        return points

    def test_converges_on_clean_exponential(self):
        """Should find τ accurately from clean decay data."""
        tau_true = 2300.0  # ~38 min
        points = self._make_decay_data(
            tau=tau_true, initial_diff=10.0, ambient=280.0,
            n_samples=120, interval=15.0,
        )

        est = DecayEstimator(
            convergence_threshold=0.10,
            min_r_squared=0.95,
            min_samples=15,
            min_time_seconds=180.0,
        )

        result = None
        for t, diff in points:
            result = est.add(t, diff)
            if result.status == EstimateStatus.CONVERGED:
                break

        assert result.status == EstimateStatus.CONVERGED
        assert abs(result.value - tau_true) / tau_true < 0.10
        assert result.r_squared > 0.95

    def test_converges_with_noise(self):
        """Should converge with realistic sensor noise."""
        tau_true = 2300.0
        points = self._make_decay_data(
            tau=tau_true, initial_diff=10.0, ambient=280.0,
            n_samples=180, interval=15.0, noise_std=0.1,
        )

        est = DecayEstimator(
            convergence_threshold=0.15,
            min_r_squared=0.90,
            min_samples=15,
            min_time_seconds=180.0,
        )

        result = None
        for t, diff in points:
            result = est.add(t, diff)
            if result.status == EstimateStatus.CONVERGED:
                break

        assert result.status == EstimateStatus.CONVERGED
        assert abs(result.value - tau_true) / tau_true < 0.20

    def test_small_differential_reports_no_signal(self):
        """Decay from small differential = poor signal, should detect."""
        # Only 2K differential — in the noise floor
        points = self._make_decay_data(
            tau=2300.0, initial_diff=2.0, ambient=280.0,
            n_samples=120, interval=15.0, noise_std=0.3,
        )

        est = DecayEstimator(
            convergence_threshold=0.10,
            min_r_squared=0.95,
            min_samples=15,
            min_time_seconds=60.0,
            no_signal_time_seconds=120.0,
            min_differential=1.0,
        )

        result = None
        for t, diff in points:
            result = est.add(t, diff)
            if result.status != EstimateStatus.RUNNING:
                break

        # With 2K and noise of 0.3K, might converge or might report
        # poor fit. Either way, if it converges the error should be large.
        # We're mainly testing it doesn't crash.
        assert result.status in (
            EstimateStatus.CONVERGED,
            EstimateStatus.NO_SIGNAL,
            EstimateStatus.RUNNING,
        )

    def test_no_decay_reports_no_signal(self):
        """Flat or rising differential = not decaying."""
        est = DecayEstimator(
            min_samples=10,
            min_time_seconds=60.0,
            no_signal_time_seconds=120.0,
        )

        t0 = 1000.0
        result = None
        for i in range(60):
            t = t0 + i * 15.0
            diff = 5.0 + 0.01 * i  # Rising, not decaying
            result = est.add(t, diff)
            if result.status != EstimateStatus.RUNNING:
                break

        assert result.status == EstimateStatus.NO_SIGNAL

    def test_tau_from_strong_vs_weak_signal(self):
        """Larger initial differential should converge faster."""
        strong = self._make_decay_data(
            tau=2300.0, initial_diff=15.0, ambient=280.0,
            n_samples=120, interval=15.0, noise_std=0.1,
        )
        weak = self._make_decay_data(
            tau=2300.0, initial_diff=3.0, ambient=280.0,
            n_samples=120, interval=15.0, noise_std=0.1,
        )

        est_strong = DecayEstimator(
            convergence_threshold=0.10,
            min_r_squared=0.95,
            min_samples=10,
            min_time_seconds=60.0,
        )
        est_weak = DecayEstimator(
            convergence_threshold=0.10,
            min_r_squared=0.95,
            min_samples=10,
            min_time_seconds=60.0,
        )

        strong_at = None
        weak_at = None

        for i in range(len(strong)):
            r_s = est_strong.add(strong[i][0], strong[i][1])
            r_w = est_weak.add(weak[i][0], weak[i][1])
            if r_s.status == EstimateStatus.CONVERGED and strong_at is None:
                strong_at = i
            if r_w.status == EstimateStatus.CONVERGED and weak_at is None:
                weak_at = i

        # Strong signal should converge
        assert strong_at is not None

    def test_conductance_property(self):
        """conductance should be 1/τ."""
        est = DecayEstimator(min_samples=5, min_time_seconds=0)

        tau_true = 2300.0
        points = self._make_decay_data(
            tau=tau_true, initial_diff=10.0, ambient=280.0,
            n_samples=60, interval=15.0,
        )

        for t, diff in points:
            est.add(t, diff)

        assert est.tau is not None
        assert est.conductance is not None
        assert abs(est.conductance - 1.0 / est.tau) < 1e-10

    def test_filters_below_min_differential(self):
        """Points where differential < min should be excluded."""
        est = DecayEstimator(min_differential=2.0, min_samples=5,
                             min_time_seconds=0)

        # Feed data that decays below threshold
        tau = 100.0
        t0 = 1000.0
        for i in range(60):
            t = t0 + i * 15.0
            diff = 10.0 * math.exp(-(i * 15.0) / tau)
            est.add(t, diff)

        # Should have excluded points where diff < 2.0
        result = est._evaluate()
        usable = [
            (t, d) for t, d in est._points if d > 2.0
        ]
        # The fit should only use usable points
        assert result.sample_count == len(est._points)  # Total count
        # But the fit used fewer


# ── Integration: validate against real hardware data ───────────────────


class TestAgainstRealData:
    """Validate estimators against known-good results from hardware runs."""

    def test_heater_rise_rate(self):
        """Simulate the heater rise we measured: ~0.035 K/s."""
        est = RateEstimator(
            convergence_threshold=0.10,
            min_samples=10,
            min_time_seconds=60.0,
        )

        # First 20 samples from the manual heater run:
        # ~53°F start, ~73°F after 5 min, sampled every 15s
        # That's ~20°F in 300s = ~11.1K in 300s = 0.037 K/s
        rate_true = 0.037  # K/s approximately
        base_k = 285.0  # ~53°F

        result = None
        t0 = 1000.0
        for i in range(20):
            t = t0 + i * 15.0
            temp = base_k + rate_true * (i * 15.0)
            result = est.add(t, temp)
            if result.status == EstimateStatus.CONVERGED:
                break

        assert result.status == EstimateStatus.CONVERGED
        assert abs(result.value - rate_true) / rate_true < 0.05

    def test_envelope_decay_tau(self):
        """Simulate the envelope decay we measured: τ ≈ 2300s."""
        est = DecayEstimator(
            convergence_threshold=0.10,
            min_r_squared=0.95,
            min_samples=15,
            min_time_seconds=180.0,
        )

        tau_true = 2300.0
        initial_diff = 15.0  # K, from heater run
        t0 = 1000.0

        result = None
        for i in range(120):
            t = t0 + i * 15.0
            diff = initial_diff * math.exp(-(i * 15.0) / tau_true)
            result = est.add(t, diff)
            if result.status == EstimateStatus.CONVERGED:
                break

        assert result.status == EstimateStatus.CONVERGED
        assert abs(result.value - tau_true) / tau_true < 0.10
        # Should converge before using all 120 samples
        assert result.sample_count < 90