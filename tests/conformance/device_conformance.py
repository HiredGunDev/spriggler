"""Device driver conformance tests.

Any device driver can be validated by subclassing DeviceConformanceTests
and providing a driver instance via fixtures.

Usage for a new driver:

    class TestMyDeviceDriver(DeviceConformanceTests):
        @pytest.fixture
        def driver(self):
            return MyDeviceDriver({"address": "192.168.1.100"})

        @pytest.fixture
        def driver_config_valid(self):
            return {"address": "192.168.1.100", "plug_index": 0}

        @pytest.fixture
        def driver_config_invalid(self):
            return {"address": "not-an-ip"}

        @pytest.fixture
        def has_countdown(self):
            return True

        @pytest.fixture
        def has_power_monitoring(self):
            return True

Run: pytest tests/test_my_device_driver.py -v
"""

import pytest

from spriggler.devices.base import DeviceDriver, DeviceCommandError


class DeviceConformanceTests:
    """Mixin test class for device driver conformance.

    Subclass this and provide the required fixtures. All conformance
    tests will run automatically.
    """

    # ── Fixtures subclasses must provide ─────────────────────────────────

    @pytest.fixture
    def driver(self):
        """Return an initialized driver instance."""
        raise NotImplementedError("Subclass must provide driver fixture")

    @pytest.fixture
    def driver_config_valid(self):
        """Return a valid driver_config dict."""
        raise NotImplementedError("Subclass must provide driver_config_valid fixture")

    @pytest.fixture
    def driver_config_invalid(self):
        """Return an invalid driver_config dict that should fail validation."""
        raise NotImplementedError("Subclass must provide driver_config_invalid fixture")

    @pytest.fixture
    def has_countdown(self):
        """Return True if the driver supports hardware countdown timers."""
        return False

    @pytest.fixture
    def has_power_monitoring(self):
        """Return True if the driver supports power monitoring."""
        return False

    # ── Identity tests ───────────────────────────────────────────────────

    def test_is_device_driver(self, driver):
        """Driver must be a subclass of DeviceDriver."""
        assert isinstance(driver, DeviceDriver)

    def test_has_driver_name(self, driver):
        """Driver must report a non-empty string name."""
        name = driver.driver_name
        assert isinstance(name, str)
        assert len(name) > 0

    # ── Command contract tests ───────────────────────────────────────────

    def test_turn_on_returns_bool(self, driver):
        """turn_on() must return a boolean."""
        try:
            result = driver.turn_on()
            assert isinstance(result, bool)
        except DeviceCommandError:
            pass  # Expected when hardware isn't available

    def test_turn_off_returns_bool(self, driver):
        """turn_off() must return a boolean."""
        try:
            result = driver.turn_off()
            assert isinstance(result, bool)
        except DeviceCommandError:
            pass  # Expected when hardware isn't available

    def test_is_on_returns_bool(self, driver):
        """is_on() must return a boolean."""
        result = driver.is_on()
        assert isinstance(result, bool)

    def test_get_power_returns_float_or_none(self, driver):
        """get_power() must return a float or None."""
        try:
            result = driver.get_power()
            assert result is None or isinstance(result, (int, float))
        except DeviceCommandError:
            pass  # Expected when hardware isn't available

    # ── Countdown tests ──────────────────────────────────────────────────

    def test_supports_countdown_returns_bool(self, driver):
        """supports_countdown() must return a boolean."""
        result = driver.supports_countdown()
        assert isinstance(result, bool)

    def test_countdown_consistency(self, driver, has_countdown):
        """supports_countdown() must match the declared capability."""
        assert driver.supports_countdown() == has_countdown

    def test_countdown_raises_if_not_supported(self, driver, has_countdown):
        """set_countdown() must raise NotImplementedError if not supported."""
        if has_countdown:
            pytest.skip("Driver supports countdown")
        with pytest.raises(NotImplementedError):
            driver.set_countdown(60, 'off')

    def test_countdown_accepts_valid_args_if_supported(self, driver, has_countdown):
        """set_countdown() must accept valid arguments if supported."""
        if not has_countdown:
            pytest.skip("Driver does not support countdown")
        try:
            result = driver.set_countdown(60, 'off')
            assert isinstance(result, bool)
        except DeviceCommandError:
            pass  # Expected when hardware isn't available

    # ── Power monitoring tests ───────────────────────────────────────────

    def test_power_none_if_not_supported(self, driver, has_power_monitoring):
        """get_power() should return None if power monitoring is not supported."""
        if has_power_monitoring:
            pytest.skip("Driver supports power monitoring")
        try:
            result = driver.get_power()
            assert result is None
        except DeviceCommandError:
            pass  # Expected when hardware isn't available

    # ── Config validation tests ──────────────────────────────────────────

    def test_valid_config_accepted(self, driver, driver_config_valid):
        """validate_config() should accept a valid config without error."""
        driver.validate_config(driver_config_valid)

    def test_invalid_config_rejected(self, driver, driver_config_invalid):
        """validate_config() should raise ValueError for invalid config."""
        with pytest.raises(ValueError):
            driver.validate_config(driver_config_invalid)
