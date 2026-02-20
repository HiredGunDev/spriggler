"""Tests for the physics model.

Every test uses synthetic calibration data with hand-computed expected
results. No real hardware, no curve fitting, no approximations. If
the model does the arithmetic right, the tests pass.

The hypothetical setup:
    - Chamber: conductance 0.5 (loses 0.5°F per °F differential per cycle)
    - Pod: conductance 0.3 (better insulated)
    - Chamber heater: adds 8°F per cycle when on
    - Pod heater: adds 6°F per cycle when on
    - Chamber exhaust: removes 4°F per cycle when on
    - Humidifier: adds 5 %RH per cycle when on
"""

import pytest

from spriggler.physics.model import predict, make_solver_predict_fn


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def calibration():
    """Synthetic calibration data for a two-environment setup."""
    return {
        "chamber": {
            "envelope": {
                "temperature": 0.5,
                "humidity": 0.1,
            },
            "devices": {
                "chamber_heater": {
                    "on":  {"temperature": 8.0},
                    "off": {"temperature": 0.0},
                },
                "chamber_exhaust": {
                    "on":  {"temperature": -4.0},
                    "off": {"temperature": 0.0},
                },
                "humidifier": {
                    "off":  {"humidity": 0.0},
                    "low":  {"humidity": 2.0},
                    "mid":  {"humidity": 5.0},
                    "high": {"humidity": 8.0},
                },
            },
        },
        "pod": {
            "envelope": {
                "temperature": 0.3,
            },
            "devices": {
                "pod_heater": {
                    "on":  {"temperature": 6.0},
                    "off": {"temperature": 0.0},
                },
            },
        },
    }


@pytest.fixture
def device_env_map():
    return {
        "chamber_heater": "chamber",
        "chamber_exhaust": "chamber",
        "humidifier": "chamber",
        "pod_heater": "pod",
    }


# ── Envelope loss tests ─────────────────────────────────────────────────────

class TestEnvelopeLoss:
    """Test heat loss toward ambient with all devices off."""

    def test_cold_day_envelope_loss(self, calibration, device_env_map):
        """Chamber at 75, ambient at 50. Loss = 0.5 × 25 = 12.5. Predicted: 62.5."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0}},
            ambient={"temperature": 50.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["temperature"] == pytest.approx(62.5)

    def test_mild_day_minimal_loss(self, calibration, device_env_map):
        """Chamber at 75, ambient at 74. Loss = 0.5 × 1 = 0.5. Predicted: 74.5."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0}},
            ambient={"temperature": 74.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["temperature"] == pytest.approx(74.5)

    def test_hot_day_gains_heat(self, calibration, device_env_map):
        """Chamber at 75, ambient at 95. Loss = 0.5 × (75-95) = -10 → gain.
        Predicted: 75 - (-10) = 85."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0}},
            ambient={"temperature": 95.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["temperature"] == pytest.approx(85.0)

    def test_at_ambient_no_loss(self, calibration, device_env_map):
        """Chamber equals ambient — zero differential, zero loss."""
        result = predict(
            current_readings={"chamber": {"temperature": 60.0}},
            ambient={"temperature": 60.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["temperature"] == pytest.approx(60.0)

    def test_pod_lower_conductance(self, calibration, device_env_map):
        """Pod at 75, ambient at 50. Loss = 0.3 × 25 = 7.5. Predicted: 67.5.
        Pod is better insulated than chamber (0.3 vs 0.5)."""
        result = predict(
            current_readings={"pod": {"temperature": 75.0}},
            ambient={"temperature": 50.0},
            proposed_states={"pod_heater": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["pod"]["temperature"] == pytest.approx(67.5)

    def test_humidity_envelope_loss(self, calibration, device_env_map):
        """Humidity also drifts toward ambient. Chamber at 60%RH, ambient at 30%RH.
        Loss = 0.1 × 30 = 3. Predicted: 57%RH."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0, "humidity": 60.0}},
            ambient={"temperature": 50.0, "humidity": 30.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["humidity"] == pytest.approx(57.0)


# ── Device contribution tests ────────────────────────────────────────────────

class TestDeviceContributions:
    """Test device effects on top of envelope loss."""

    def test_heater_on_cold_day(self, calibration, device_env_map):
        """Chamber at 75, ambient at 50. Loss=12.5, heater adds 8.
        Predicted: 75 - 12.5 + 8 = 70.5."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0}},
            ambient={"temperature": 50.0},
            proposed_states={"chamber_heater": "on", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["temperature"] == pytest.approx(70.5)

    def test_exhaust_on(self, calibration, device_env_map):
        """Chamber at 85, ambient at 75. Loss = 0.5 × 10 = 5. Exhaust removes 4.
        Predicted: 85 - 5 - 4 = 76."""
        result = predict(
            current_readings={"chamber": {"temperature": 85.0}},
            ambient={"temperature": 75.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "on",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["temperature"] == pytest.approx(76.0)

    def test_heater_and_exhaust_together(self, calibration, device_env_map):
        """Both heater and exhaust running. Chamber at 75, ambient at 50.
        Loss=12.5, heater +8, exhaust -4. Predicted: 75 - 12.5 + 8 - 4 = 66.5."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0}},
            ambient={"temperature": 50.0},
            proposed_states={"chamber_heater": "on", "chamber_exhaust": "on",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["temperature"] == pytest.approx(66.5)

    def test_pod_heater(self, calibration, device_env_map):
        """Pod at 70, ambient at 50. Loss = 0.3 × 20 = 6. Heater adds 6.
        Predicted: 70 - 6 + 6 = 70. Heater exactly offsets envelope loss."""
        result = predict(
            current_readings={"pod": {"temperature": 70.0}},
            ambient={"temperature": 50.0},
            proposed_states={"pod_heater": "on"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["pod"]["temperature"] == pytest.approx(70.0)

    def test_device_only_affects_its_environment(self, calibration, device_env_map):
        """Chamber heater should not affect pod temperature."""
        result = predict(
            current_readings={
                "chamber": {"temperature": 75.0},
                "pod": {"temperature": 70.0},
            },
            ambient={"temperature": 50.0},
            proposed_states={
                "chamber_heater": "on",
                "chamber_exhaust": "off",
                "humidifier": "off",
                "pod_heater": "off",
            },
            calibration=calibration,
            device_env_map=device_env_map,
        )
        # Pod: 70 - 0.3 × (70-50) = 70 - 6 = 64. No chamber heater effect.
        assert result["pod"]["temperature"] == pytest.approx(64.0)
        # Chamber: 75 - 0.5 × 25 + 8 = 70.5
        assert result["chamber"]["temperature"] == pytest.approx(70.5)


# ── Graduated device tests ───────────────────────────────────────────────────

class TestGraduatedDevices:
    """Test graduated device levels produce correct predictions."""

    def test_humidifier_off(self, calibration, device_env_map):
        """Humidifier off adds nothing."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0, "humidity": 50.0}},
            ambient={"temperature": 75.0, "humidity": 50.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["humidity"] == pytest.approx(50.0)

    def test_humidifier_low(self, calibration, device_env_map):
        """Humidifier on low adds 2%RH. At ambient parity, no drift.
        Predicted: 50 + 2 = 52."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0, "humidity": 50.0}},
            ambient={"temperature": 75.0, "humidity": 50.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "low"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["humidity"] == pytest.approx(52.0)

    def test_humidifier_mid(self, calibration, device_env_map):
        """Humidifier on mid adds 5%RH."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0, "humidity": 50.0}},
            ambient={"temperature": 75.0, "humidity": 50.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "mid"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["humidity"] == pytest.approx(55.0)

    def test_humidifier_high(self, calibration, device_env_map):
        """Humidifier on high adds 8%RH."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0, "humidity": 50.0}},
            ambient={"temperature": 75.0, "humidity": 50.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "high"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["humidity"] == pytest.approx(58.0)

    def test_humidifier_with_drift(self, calibration, device_env_map):
        """Humidifier mid with humidity drift. Chamber 60%RH, ambient 30%RH.
        Drift = 0.1 × 30 = 3%RH loss. Humidifier mid adds 5.
        Predicted: 60 - 3 + 5 = 62."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0, "humidity": 60.0}},
            ambient={"temperature": 50.0, "humidity": 30.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "mid"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["chamber"]["humidity"] == pytest.approx(62.0)


# ── Multi-environment tests ──────────────────────────────────────────────────

class TestMultiEnvironment:
    """Test simultaneous prediction across multiple environments."""

    def test_both_environments_predicted(self, calibration, device_env_map):
        """Both chamber and pod should get predictions."""
        result = predict(
            current_readings={
                "chamber": {"temperature": 75.0},
                "pod": {"temperature": 70.0},
            },
            ambient={"temperature": 50.0},
            proposed_states={
                "chamber_heater": "on",
                "chamber_exhaust": "off",
                "humidifier": "off",
                "pod_heater": "on",
            },
            calibration=calibration,
            device_env_map=device_env_map,
        )
        # Chamber: 75 - 0.5×25 + 8 = 70.5
        assert result["chamber"]["temperature"] == pytest.approx(70.5)
        # Pod: 70 - 0.3×20 + 6 = 70
        assert result["pod"]["temperature"] == pytest.approx(70.0)

    def test_independent_environments(self, calibration, device_env_map):
        """Different device states per environment."""
        result = predict(
            current_readings={
                "chamber": {"temperature": 80.0},
                "pod": {"temperature": 60.0},
            },
            ambient={"temperature": 50.0},
            proposed_states={
                "chamber_heater": "off",      # Chamber hot, heater off
                "chamber_exhaust": "on",       # Run exhaust to cool
                "humidifier": "off",
                "pod_heater": "on",            # Pod cold, heater on
            },
            calibration=calibration,
            device_env_map=device_env_map,
        )
        # Chamber: 80 - 0.5×30 - 4 = 80 - 15 - 4 = 61
        assert result["chamber"]["temperature"] == pytest.approx(61.0)
        # Pod: 60 - 0.3×10 + 6 = 60 - 3 + 6 = 63
        assert result["pod"]["temperature"] == pytest.approx(63.0)


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_uncalibrated_environment_returns_current(self, calibration, device_env_map):
        """Environment without calibration data returns current readings unchanged."""
        result = predict(
            current_readings={"mystery_room": {"temperature": 72.0}},
            ambient={"temperature": 50.0},
            proposed_states={},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert result["mystery_room"]["temperature"] == pytest.approx(72.0)

    def test_ambient_environment_excluded(self, calibration, device_env_map):
        """Ambient environment should not appear in predictions."""
        result = predict(
            current_readings={
                "ambient": {"temperature": 50.0},
                "chamber": {"temperature": 75.0},
            },
            ambient={"temperature": 50.0},
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        assert "ambient" not in result
        assert "chamber" in result

    def test_unknown_device_state_adds_nothing(self, calibration, device_env_map):
        """Device state not in calibration contributes zero."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0}},
            ambient={"temperature": 75.0},
            proposed_states={"chamber_heater": "turbo", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        # "turbo" isn't calibrated, so no contribution. No drift (ambient=current).
        assert result["chamber"]["temperature"] == pytest.approx(75.0)

    def test_missing_ambient_property_skips_envelope(self, calibration, device_env_map):
        """If ambient doesn't have a property, envelope loss is skipped for it."""
        result = predict(
            current_readings={"chamber": {"temperature": 75.0, "humidity": 60.0}},
            ambient={"temperature": 50.0},  # No humidity in ambient
            proposed_states={"chamber_heater": "off", "chamber_exhaust": "off",
                             "humidifier": "off"},
            calibration=calibration,
            device_env_map=device_env_map,
        )
        # Temperature has envelope loss: 75 - 0.5×25 = 62.5
        assert result["chamber"]["temperature"] == pytest.approx(62.5)
        # Humidity has no ambient reference — stays at current
        assert result["chamber"]["humidity"] == pytest.approx(60.0)

    def test_property_without_conductance_no_drift(self, device_env_map):
        """Property not in envelope calibration has no drift."""
        cal = {
            "chamber": {
                "envelope": {},  # No conductance calibrated
                "devices": {},
            }
        }
        result = predict(
            current_readings={"chamber": {"temperature": 75.0}},
            ambient={"temperature": 50.0},
            proposed_states={},
            calibration=cal,
            device_env_map=device_env_map,
        )
        # No conductance → no drift. Stays at 75.
        assert result["chamber"]["temperature"] == pytest.approx(75.0)


# ── Solver integration tests ────────────────────────────────────────────────

class TestSolverIntegration:
    """Test make_solver_predict_fn produces correct predictions."""

    def test_solver_predict_fn_matches_direct(self, calibration, device_env_map):
        """Factory function should produce same results as calling predict directly."""
        current = {"chamber": {"temperature": 75.0}, "pod": {"temperature": 70.0}}
        ambient = {"temperature": 50.0}
        states = {
            "chamber_heater": "on",
            "chamber_exhaust": "off",
            "humidifier": "off",
            "pod_heater": "on",
        }

        direct = predict(current, ambient, states, calibration, device_env_map)
        solver_fn = make_solver_predict_fn(ambient, calibration, device_env_map)
        via_factory = solver_fn(current, states)

        for env_id in direct:
            for prop in direct[env_id]:
                assert via_factory[env_id][prop] == pytest.approx(direct[env_id][prop])

    def test_solver_predict_fn_different_states(self, calibration, device_env_map):
        """Same factory, different proposed states should give different results."""
        current = {"chamber": {"temperature": 75.0}}
        ambient = {"temperature": 50.0}
        solver_fn = make_solver_predict_fn(ambient, calibration, device_env_map)

        heater_on = solver_fn(current, {"chamber_heater": "on", "chamber_exhaust": "off",
                                         "humidifier": "off"})
        heater_off = solver_fn(current, {"chamber_heater": "off", "chamber_exhaust": "off",
                                          "humidifier": "off"})

        # Heater on: 75 - 12.5 + 8 = 70.5
        # Heater off: 75 - 12.5 = 62.5
        assert heater_on["chamber"]["temperature"] == pytest.approx(70.5)
        assert heater_off["chamber"]["temperature"] == pytest.approx(62.5)

