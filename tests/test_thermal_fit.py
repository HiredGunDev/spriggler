"""Tests for spriggler.calibrate.thermal_fit - curve fitting."""

import math

import pytest

from spriggler.calibrate.thermal_fit import (
    ThermalSample,
    DecayFit,
    RiseFit,
    fit_decay,
    fit_rise,
    _linear_least_squares,
)


# ── Helpers ──────────────────────────────────────────────────────────

def make_decay_samples(
        t0: float = 0.0,
        ambient: float = 290.0,
        initial_diff: float = 15.0,
        tau: float = 600.0,
        interval: float = 10.0,
        count: int = 60,
        noise: float = 0.0,
) -> list[ThermalSample]:
    """Generate synthetic exponential decay samples.

    T(t) = ambient + initial_diff × e^(-t/τ)
    """
    import random
    samples = []
    for i in range(count):
        t = t0 + i * interval
        diff = initial_diff * math.exp(-i * interval / tau)
        interior = ambient + diff
        if noise > 0:
            interior += random.gauss(0, noise)
        samples.append(ThermalSample(
            timestamp=t,
            interior=interior,
            ambient=ambient,
        ))
    return samples


def make_rise_samples(
        t0: float = 0.0,
        ambient: float = 290.0,
        initial_diff: float = 0.0,
        rise_rate: float = 0.02,
        interval: float = 10.0,
        count: int = 30,
) -> list[ThermalSample]:
    """Generate synthetic linear rise samples.

    T(t) = ambient + initial_diff + rise_rate × t
    (Linear approximation of early rise phase)
    """
    samples = []
    for i in range(count):
        t = t0 + i * interval
        diff = initial_diff + rise_rate * (i * interval)
        samples.append(ThermalSample(
            timestamp=t,
            interior=ambient + diff,
            ambient=ambient,
        ))
    return samples


# ── ThermalSample ────────────────────────────────────────────────────

class TestThermalSample:

    def test_differential(self):
        s = ThermalSample(timestamp=0, interior=300.0, ambient=290.0)
        assert s.differential == 10.0

    def test_negative_differential(self):
        s = ThermalSample(timestamp=0, interior=285.0, ambient=290.0)
        assert s.differential == -5.0

    def test_zero_differential(self):
        s = ThermalSample(timestamp=0, interior=290.0, ambient=290.0)
        assert s.differential == 0.0


# ── _linear_least_squares ────────────────────────────────────────────

class TestLinearLeastSquares:

    def test_perfect_line(self):
        xs = [0, 1, 2, 3, 4]
        ys = [2, 5, 8, 11, 14]  # y = 3x + 2
        slope, intercept, r_sq, slope_se = _linear_least_squares(xs, ys)
        assert abs(slope - 3.0) < 1e-10
        assert abs(intercept - 2.0) < 1e-10
        assert abs(r_sq - 1.0) < 1e-10

    def test_negative_slope(self):
        xs = [0, 1, 2, 3]
        ys = [10, 7, 4, 1]  # y = -3x + 10
        slope, intercept, r_sq, slope_se = _linear_least_squares(xs, ys)
        assert abs(slope - (-3.0)) < 1e-10
        assert abs(intercept - 10.0) < 1e-10

    def test_noisy_data_r_squared(self):
        """R² should be less than 1 for noisy data."""
        xs = [0, 1, 2, 3, 4]
        ys = [2.1, 4.8, 8.2, 10.9, 14.1]
        slope, intercept, r_sq, slope_se = _linear_least_squares(xs, ys)
        assert 0.99 < r_sq < 1.0  # Close but not perfect

    def test_two_points(self):
        """Minimum case: exactly two points."""
        xs = [0, 10]
        ys = [5, 15]
        slope, intercept, r_sq, slope_se = _linear_least_squares(xs, ys)
        assert abs(slope - 1.0) < 1e-10
        assert abs(intercept - 5.0) < 1e-10

    def test_one_point_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            _linear_least_squares([1], [1])

    def test_identical_x_raises(self):
        with pytest.raises(ValueError, match="identical"):
            _linear_least_squares([5, 5, 5], [1, 2, 3])


# ── fit_decay ────────────────────────────────────────────────────────

class TestFitDecay:

    def test_perfect_exponential(self):
        """Recover known τ from clean synthetic data."""
        tau_true = 600.0
        samples = make_decay_samples(tau=tau_true, count=60, interval=10)
        result = fit_decay(samples)

        assert abs(result.tau - tau_true) < 1.0
        assert abs(result.conductance - 1.0 / tau_true) < 1e-5
        assert result.r_squared > 0.999
        assert result.sample_count > 0

    def test_fast_decay(self):
        """Short time constant (leaky envelope)."""
        tau_true = 120.0
        samples = make_decay_samples(tau=tau_true, count=30, interval=5)
        result = fit_decay(samples)
        assert abs(result.tau - tau_true) < 2.0

    def test_slow_decay(self):
        """Long time constant (well-insulated)."""
        tau_true = 3600.0
        samples = make_decay_samples(tau=tau_true, count=100, interval=30)
        result = fit_decay(samples)
        assert abs(result.tau - tau_true) / tau_true < 0.01

    def test_noisy_data(self):
        """Fit still reasonable with sensor noise."""
        tau_true = 600.0
        samples = make_decay_samples(
            tau=tau_true, count=100, interval=10, noise=0.3
        )
        result = fit_decay(samples)
        # Allow 5% error with noise
        assert abs(result.tau - tau_true) / tau_true < 0.05
        assert result.r_squared > 0.95

    def test_varying_ambient(self):
        """Ambient drift during decay doesn't wreck the fit too badly."""
        tau_true = 600.0
        samples = make_decay_samples(tau=tau_true, count=60, interval=10)
        # Simulate ambient rising 1K over the experiment
        for i, s in enumerate(samples):
            drift = (i / len(samples)) * 1.0
            samples[i] = ThermalSample(
                timestamp=s.timestamp,
                interior=s.interior,
                ambient=s.ambient + drift,
            )
        result = fit_decay(samples)
        # Looser tolerance — ambient drift biases the fit.
        # 1K drift over 10 minutes can cause ~16% tau error.
        # This is real physics, not a bug — it's why we want
        # stable ambient during calibration.
        assert abs(result.tau - tau_true) / tau_true < 0.20

    def test_initial_differential_recovered(self):
        """Fit recovers the starting differential."""
        init_diff = 20.0
        samples = make_decay_samples(initial_diff=init_diff, tau=600)
        result = fit_decay(samples)
        assert abs(result.initial_differential - init_diff) < 0.5

    def test_too_few_samples_raises(self):
        samples = make_decay_samples(count=3)
        with pytest.raises(ValueError, match="at least 5"):
            fit_decay(samples)

    def test_near_ambient_filtered(self):
        """Samples near ambient (small differential) are filtered out."""
        # Decay that reaches near-ambient quickly
        samples = make_decay_samples(
            initial_diff=2.0, tau=30.0, count=60, interval=5
        )
        # Most samples will be below min_differential=1.0
        # Should still work with the early samples
        result = fit_decay(samples, min_differential=0.5)
        assert result.sample_count > 0

    def test_non_decaying_raises(self):
        """Rising temperature during 'decay' phase raises error."""
        samples = make_rise_samples(rise_rate=0.01, count=20)
        # These have increasing differential — log transform will
        # give positive slope
        with pytest.raises(ValueError, match="not decaying"):
            fit_decay(samples)


# ── fit_rise ─────────────────────────────────────────────────────────

class TestFitRise:

    def test_basic_rise(self):
        """Recover rise rate from clean linear data."""
        rate = 0.02  # K/s
        samples = make_rise_samples(rise_rate=rate, count=30, interval=10)
        result = fit_rise(samples)

        assert abs(result.gross_rise_rate - rate) < 1e-6
        assert result.sample_count == 30
        assert result.duration_seconds > 0

    def test_net_rate_with_envelope(self):
        """Net rise rate accounts for envelope loss."""
        rate = 0.02  # K/s observed
        conductance = 0.001  # 1/s
        samples = make_rise_samples(
            rise_rate=rate, count=30, interval=10, initial_diff=5.0
        )
        result = fit_rise(samples, envelope_conductance=conductance)

        # Net should be higher than gross (device works harder than
        # the temperature rise suggests, because envelope is leaking)
        assert result.net_rise_rate > result.gross_rise_rate

    def test_without_envelope(self):
        """Without envelope data, net equals gross."""
        rate = 0.02
        samples = make_rise_samples(rise_rate=rate, count=20, interval=10)
        result = fit_rise(samples, envelope_conductance=None)
        assert result.net_rise_rate == result.gross_rise_rate

    def test_with_power(self):
        """Power data is recorded when provided."""
        samples = make_rise_samples(rise_rate=0.02, count=20, interval=10)
        result = fit_rise(samples, power_watts=620.0)
        assert result.power_watts_electrical == 620.0

    def test_peak_differential(self):
        """Peak differential is the maximum observed."""
        samples = make_rise_samples(
            rise_rate=0.05, count=20, interval=10, initial_diff=2.0
        )
        result = fit_rise(samples)
        # Last sample should have highest differential
        expected_peak = 2.0 + 0.05 * 19 * 10
        assert abs(result.peak_differential - expected_peak) < 0.01

    def test_too_few_samples_raises(self):
        samples = make_rise_samples(count=2)
        with pytest.raises(ValueError, match="at least 3"):
            fit_rise(samples)

    def test_zero_duration_raises(self):
        """All samples at same timestamp raises."""
        samples = [
            ThermalSample(timestamp=100, interior=300, ambient=290),
            ThermalSample(timestamp=100, interior=301, ambient=290),
            ThermalSample(timestamp=100, interior=302, ambient=290),
        ]
        with pytest.raises(ValueError, match="too short"):
            fit_rise(samples)