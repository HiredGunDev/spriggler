"""Tests for calibration data structures and serialization."""

import json
import time
import pytest
from pathlib import Path

from spriggler.calibrate.engine import (
    PropertyCalibration,
    StateCalibration,
    DeviceCalibration,
    EnvironmentCalibration,
    save_calibration,
    load_calibration,
    _cal_to_dict,
    _dict_to_cal,
)


def _make_sample_cal() -> EnvironmentCalibration:
    """Create a realistic sample calibration for testing."""
    heater_on = StateCalibration(
        state="on",
        power_draw=1230.0,
        properties={
            "temperature": PropertyCalibration(
                property_name="temperature",
                rate=0.0055,         # ~0.6°F/min
                coast_overshoot=1.5,  # K
                coast_duration=120.0,
                coast_profile=[(0, 300.0), (30, 300.8), (60, 301.2), (120, 301.5)],
            ),
        },
    )

    humidifier_low = StateCalibration(
        state="low",
        properties={
            "absolute_humidity": PropertyCalibration(
                property_name="absolute_humidity",
                rate=0.008,          # g/m³/s
                coast_overshoot=0.3,
                coast_duration=60.0,
                coast_profile=[(0, 15.0), (30, 15.2), (60, 15.3)],
            ),
        },
        thermal_byproduct_rate=0.0001,  # Tiny heat from the motor
    )

    humidifier_high = StateCalibration(
        state="high",
        properties={
            "absolute_humidity": PropertyCalibration(
                property_name="absolute_humidity",
                rate=0.015,
                coast_overshoot=0.5,
                coast_duration=90.0,
                coast_profile=[(0, 15.0), (30, 15.4), (60, 15.5), (90, 15.5)],
            ),
        },
        thermal_byproduct_rate=0.0002,
    )

    cal = EnvironmentCalibration(
        environment_name="seedling",
        calibrated_at=time.time(),
        passive_conductance={
            "temperature": 0.0003,
            "absolute_humidity": 0.0001,
        },
        time_constant={
            "temperature": 3333.0,
            "absolute_humidity": 10000.0,
        },
    )
    cal.devices["seedling_heater"] = DeviceCalibration(
        device_name="seedling_heater",
        states={"on": heater_on},
        calibrated_at=time.time(),
        ambient_temp_during_cal=274.15,  # ~34°F
    )
    cal.devices["seedling_humidifier"] = DeviceCalibration(
        device_name="seedling_humidifier",
        states={"low": humidifier_low, "high": humidifier_high},
        calibrated_at=time.time(),
    )
    return cal


class TestCalibrationDataStructures:
    """Test the calibration data structures."""

    def test_property_calibration(self):
        pcal = PropertyCalibration(
            property_name="temperature",
            rate=0.005,
            coast_overshoot=1.5,
            coast_duration=120.0,
            coast_profile=[(0, 300.0), (60, 301.0)],
        )
        assert pcal.rate == 0.005
        assert pcal.coast_overshoot == 1.5

    def test_state_calibration(self):
        scal = StateCalibration(state="on", power_draw=1200.0)
        assert scal.state == "on"
        assert scal.power_draw == 1200.0
        assert scal.thermal_byproduct_rate is None

    def test_device_calibration(self):
        dcal = DeviceCalibration(device_name="heater")
        assert dcal.device_name == "heater"
        assert dcal.states == {}

    def test_environment_calibration(self):
        cal = _make_sample_cal()
        assert cal.environment_name == "seedling"
        assert "seedling_heater" in cal.devices
        assert "seedling_humidifier" in cal.devices
        assert "temperature" in cal.passive_conductance


class TestCalibrationSerialization:
    """Test save/load round-trip."""

    def test_to_dict(self):
        cal = _make_sample_cal()
        data = _cal_to_dict(cal)
        assert data["environment"] == "seedling"
        assert "seedling_heater" in data["devices"]
        assert "on" in data["devices"]["seedling_heater"]["states"]
        assert "temperature" in data["devices"]["seedling_heater"]["states"]["on"]["properties"]

    def test_round_trip(self):
        """Serialize → deserialize should preserve all data."""
        cal = _make_sample_cal()
        data = _cal_to_dict(cal)
        restored = _dict_to_cal(data)

        assert restored.environment_name == cal.environment_name
        assert restored.passive_conductance == cal.passive_conductance

        # Check heater
        heater = restored.devices["seedling_heater"]
        assert "on" in heater.states
        temp_cal = heater.states["on"].properties["temperature"]
        assert temp_cal.rate == pytest.approx(0.0055)
        assert temp_cal.coast_overshoot == pytest.approx(1.5)
        assert heater.states["on"].power_draw == pytest.approx(1230.0)

        # Check humidifier graduated states
        hum = restored.devices["seedling_humidifier"]
        assert "low" in hum.states
        assert "high" in hum.states
        assert hum.states["low"].thermal_byproduct_rate == pytest.approx(0.0001)
        assert hum.states["high"].properties["absolute_humidity"].rate == pytest.approx(0.015)

    def test_save_and_load(self, tmp_path):
        """Save to file, load back, verify."""
        cal = _make_sample_cal()
        filepath = save_calibration(cal, tmp_path)
        assert filepath.is_file()

        loaded = load_calibration("seedling", tmp_path)
        assert loaded is not None
        assert loaded.environment_name == "seedling"
        assert "seedling_heater" in loaded.devices

    def test_load_nonexistent(self, tmp_path):
        """Loading a nonexistent calibration returns None."""
        assert load_calibration("nonexistent", tmp_path) is None

    def test_json_is_valid(self, tmp_path):
        """The saved file is valid JSON."""
        cal = _make_sample_cal()
        filepath = save_calibration(cal, tmp_path)
        with open(filepath) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data["environment"] == "seedling"

    def test_coast_profile_preserved(self, tmp_path):
        """Coast profiles survive serialization."""
        cal = _make_sample_cal()
        save_calibration(cal, tmp_path)
        loaded = load_calibration("seedling", tmp_path)

        profile = loaded.devices["seedling_heater"].states["on"] \
            .properties["temperature"].coast_profile
        assert len(profile) == 4
        assert profile[0] == [0, 300.0]  # JSON converts tuples to lists

    def test_rate_raw_preserved(self, tmp_path):
        """rate_raw (pre-compensation) survives serialization."""
        cal = _make_sample_cal()
        # Add rate_raw to a humidifier property
        hum_low = cal.devices["seedling_humidifier"].states["low"]
        hum_low.properties["absolute_humidity"].rate_raw = 0.006
        save_calibration(cal, tmp_path)
        loaded = load_calibration("seedling", tmp_path)
        ah_cal = loaded.devices["seedling_humidifier"].states["low"] \
            .properties["absolute_humidity"]
        assert ah_cal.rate_raw == pytest.approx(0.006)
        assert ah_cal.rate == pytest.approx(0.008)  # compensated rate

    def test_avg_temp_preserved(self, tmp_path):
        """avg_temp_during_activation survives serialization."""
        cal = _make_sample_cal()
        cal.devices["seedling_humidifier"].states["low"].avg_temp_during_activation = 299.8
        save_calibration(cal, tmp_path)
        loaded = load_calibration("seedling", tmp_path)
        assert loaded.devices["seedling_humidifier"].states["low"] \
            .avg_temp_during_activation == pytest.approx(299.8)


class TestAHCompensation:
    """Test absolute humidity temperature compensation."""

    def test_constant_temperature_no_change(self):
        """If temperature doesn't change, compensation is a no-op."""
        from spriggler.calibrate.engine import _compensate_ah_for_temperature

        baseline_temp = 300.0  # ~80°F
        baseline_ah = 15.0

        ah_series = [(0, 15.0), (10, 16.0), (20, 17.0)]
        temp_series = [(0, 300.0), (10, 300.0), (20, 300.0)]

        compensated = _compensate_ah_for_temperature(
            ah_series, temp_series, baseline_temp, baseline_ah,
        )
        # With constant temperature, compensated should equal original
        for (t_orig, v_orig), (t_comp, v_comp) in zip(ah_series, compensated):
            assert v_comp == pytest.approx(v_orig, abs=0.01)

    def test_cooling_without_moisture_addition(self):
        """Cooling with no moisture change should compensate to flat."""
        from spriggler.calibrate.engine import (
            _compensate_ah_for_temperature,
            _ah_from_temp_and_rh,
            _rh_from_temp_and_ah,
        )

        baseline_temp = 300.0  # ~80°F
        baseline_ah = 15.0
        baseline_rh = _rh_from_temp_and_ah(baseline_temp, baseline_ah)

        # Temperature drops, but %RH stays the same (constant water content)
        # AH drops because air capacity decreases
        temps = [300.0, 298.0, 296.0]
        ah_values = [_ah_from_temp_and_rh(t, baseline_rh) for t in temps]

        ah_series = [(i * 10, ah) for i, ah in enumerate(ah_values)]
        temp_series = [(i * 10, t) for i, t in enumerate(temps)]

        compensated = _compensate_ah_for_temperature(
            ah_series, temp_series, baseline_temp, baseline_ah,
        )
        # Compensated values should all be close to baseline
        for t, v in compensated:
            assert v == pytest.approx(baseline_ah, abs=0.05)

    def test_moisture_addition_detected(self):
        """Moisture added during cooling should show positive rate."""
        from spriggler.calibrate.engine import (
            _compensate_ah_for_temperature,
            _ah_from_temp_and_rh,
            _rh_from_temp_and_ah,
        )

        baseline_temp = 300.0
        baseline_ah = 15.0
        baseline_rh = _rh_from_temp_and_ah(baseline_temp, baseline_ah)

        # Temperature drops, but we ADD moisture (RH increases)
        temps = [300.0, 298.0, 296.0]
        rhs = [baseline_rh, baseline_rh + 0.05, baseline_rh + 0.10]
        ah_values = [_ah_from_temp_and_rh(t, rh) for t, rh in zip(temps, rhs)]

        ah_series = [(i * 10, ah) for i, ah in enumerate(ah_values)]
        temp_series = [(i * 10, t) for i, t in enumerate(temps)]

        compensated = _compensate_ah_for_temperature(
            ah_series, temp_series, baseline_temp, baseline_ah,
        )
        # Compensated should show an increase (moisture was added)
        assert compensated[-1][1] > compensated[0][1]
