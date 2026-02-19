"""Sensor driver conformance tests.

Any sensor driver can be validated by subclassing SensorConformanceTests
and providing a driver instance and sample data via fixtures.

Usage for a new driver:

    class TestMyDriver(SensorConformanceTests):
        @pytest.fixture
        def driver(self):
            return MyDriver({"address": "AA:BB:CC:DD:EE:FF"})

        @pytest.fixture
        def sample_reading(self):
            # What read() returns under normal conditions
            return {
                "temperature": 295.37,
                "humidity": 63.2,
                "battery": 87,
                "signal_strength": -72
            }

        @pytest.fixture
        def driver_config_valid(self):
            return {"address": "AA:BB:CC:DD:EE:FF"}

        @pytest.fixture
        def driver_config_invalid(self):
            return {"address": "not-a-mac"}

Run: pytest tests/test_my_driver.py -v
"""

import pytest

from spriggler.sensors.base import SensorDriver, SensorReadError


class SensorConformanceTests:
    """Mixin test class for sensor driver conformance.

    Subclass this and provide the required fixtures. All conformance
    tests will run automatically.
    """

    # ── Fixtures subclasses must provide ─────────────────────────────────

    @pytest.fixture
    def driver(self):
        """Return an initialized driver instance."""
        raise NotImplementedError("Subclass must provide driver fixture")

    @pytest.fixture
    def sample_reading(self):
        """Return a dict representing a typical read() result."""
        raise NotImplementedError("Subclass must provide sample_reading fixture")

    @pytest.fixture
    def driver_config_valid(self):
        """Return a valid driver_config dict."""
        raise NotImplementedError("Subclass must provide driver_config_valid fixture")

    @pytest.fixture
    def driver_config_invalid(self):
        """Return an invalid driver_config dict that should fail validation."""
        raise NotImplementedError("Subclass must provide driver_config_invalid fixture")

    # ── Identity tests ───────────────────────────────────────────────────

    def test_is_sensor_driver(self, driver):
        """Driver must be a subclass of SensorDriver."""
        assert isinstance(driver, SensorDriver)

    def test_has_driver_name(self, driver):
        """Driver must report a non-empty string name."""
        name = driver.driver_name
        assert isinstance(name, str)
        assert len(name) > 0

    # ── Read contract tests ──────────────────────────────────────────────

    def test_sample_reading_is_dict(self, sample_reading):
        """read() must return a dict."""
        assert isinstance(sample_reading, dict)

    def test_sample_reading_keys_are_strings(self, sample_reading):
        """All keys in read() result must be strings."""
        for key in sample_reading:
            assert isinstance(key, str), f"Key {key!r} is not a string"

    def test_sample_reading_keys_in_taxonomy(self, sample_reading):
        """All keys must be in the standard taxonomy."""
        invalid = set(sample_reading.keys()) - SensorDriver.VALID_KEYS
        assert not invalid, (
            f"Keys not in standard taxonomy: {invalid}. "
            f"Valid keys: {sorted(SensorDriver.VALID_KEYS)}"
        )

    def test_sample_reading_values_are_numeric(self, sample_reading):
        """All values must be numeric (int or float)."""
        for key, value in sample_reading.items():
            assert isinstance(value, (int, float)), (
                f"Value for '{key}' is {type(value).__name__}, "
                f"expected int or float"
            )

    # ── SI unit validation ───────────────────────────────────────────────

    def test_temperature_is_kelvin(self, sample_reading):
        """If temperature is present, it must be in Kelvin (> 200)."""
        if 'temperature' not in sample_reading:
            pytest.skip("No temperature in sample reading")
        temp = sample_reading['temperature']
        assert temp > 200, (
            f"Temperature {temp} looks like Celsius or Fahrenheit, "
            f"not Kelvin. Expected > 200 K."
        )

    def test_humidity_is_percent(self, sample_reading):
        """If humidity is present, it must be %RH (0-100)."""
        if 'humidity' not in sample_reading:
            pytest.skip("No humidity in sample reading")
        rh = sample_reading['humidity']
        assert 0 <= rh <= 100, (
            f"Humidity {rh} outside 0-100 range for %RH"
        )

    def test_battery_is_percent(self, sample_reading):
        """If battery is present, it must be percent (0-100)."""
        if 'battery' not in sample_reading:
            pytest.skip("No battery in sample reading")
        batt = sample_reading['battery']
        assert 0 <= batt <= 100, (
            f"Battery {batt} outside 0-100 range"
        )

    def test_signal_strength_is_negative_dbm(self, sample_reading):
        """If signal_strength is present, it must be negative dBm."""
        if 'signal_strength' not in sample_reading:
            pytest.skip("No signal_strength in sample reading")
        rssi = sample_reading['signal_strength']
        assert rssi < 0, (
            f"Signal strength {rssi} should be negative (dBm)"
        )

    # ── Config validation tests ──────────────────────────────────────────

    def test_valid_config_accepted(self, driver, driver_config_valid):
        """validate_config() should accept a valid config without error."""
        driver.validate_config(driver_config_valid)

    def test_invalid_config_rejected(self, driver, driver_config_invalid):
        """validate_config() should raise ValueError for invalid config."""
        with pytest.raises(ValueError):
            driver.validate_config(driver_config_invalid)

    # ── Return type tests ────────────────────────────────────────────────

    def test_read_returns_dict_or_none(self, driver):
        """read() must return a dict or None, never raise unexpectedly.

        Note: This calls read() on the actual driver. For hardware
        drivers without hardware present, read() should return None
        or raise SensorReadError, not crash with an unhandled exception.
        """
        try:
            result = driver.read()
            assert result is None or isinstance(result, dict)
        except SensorReadError:
            pass  # Expected when hardware isn't available
