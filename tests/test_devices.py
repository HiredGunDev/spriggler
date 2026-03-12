"""Tests for device base class, registry, and driver discovery."""

import pytest

from spriggler.devices import DeviceDriver, DeviceCommandError, driver_registry


class MockDevice(DeviceDriver):
    """Minimal mock for testing the base class contract."""

    def __init__(self, device_name: str, driver_config: dict) -> None:
        self._device_name = device_name
        self._states = driver_config.get("states", ["off", "on"])
        self._last_commanded_state = "off"

    def set_state(self, state: str) -> bool:
        if state not in self._states:
            raise ValueError(f"Invalid state '{state}'")
        self._last_commanded_state = state
        return True

    def get_states(self) -> list[str]:
        return list(self._states)

    def validate_config(self, driver_config: dict) -> None:
        pass

    @property
    def driver_name(self) -> str:
        return "mock"


class TestDeviceDriver:
    """Test the device driver base class."""

    def test_binary_device(self):
        dev = MockDevice("test", {})
        assert dev.get_states() == ["off", "on"]
        assert dev.last_commanded_state == "off"

        dev.set_state("on")
        assert dev.last_commanded_state == "on"

        dev.set_state("off")
        assert dev.last_commanded_state == "off"

    def test_graduated_device(self):
        dev = MockDevice("test", {"states": ["off", "low", "high"]})
        assert dev.get_states() == ["off", "low", "high"]

        dev.set_state("low")
        assert dev.last_commanded_state == "low"

        dev.set_state("high")
        assert dev.last_commanded_state == "high"

    def test_invalid_state_raises(self):
        dev = MockDevice("test", {})
        with pytest.raises(ValueError, match="Invalid state"):
            dev.set_state("turbo")

    def test_device_name(self):
        dev = MockDevice("seedling_heater", {})
        assert dev.device_name == "seedling_heater"

    def test_countdown_not_supported_by_default(self):
        dev = MockDevice("test", {})
        assert dev.supports_countdown() is False
        with pytest.raises(NotImplementedError):
            dev.set_countdown(300)


class TestDeviceRegistry:
    """Test device driver registry."""

    def test_register_and_get(self):
        driver_registry.register("mock_test", MockDevice)
        assert driver_registry.has_driver("mock_test")
        assert driver_registry.get("mock_test") is MockDevice

    def test_unknown_returns_none(self):
        assert driver_registry.get("nonexistent_xyz") is None

    def test_kasa_registered_via_discovery(self):
        """KASA driver registers itself when discovered."""
        from spriggler.util.discovery import discover_plugins
        discover_plugins(package="spriggler.devices", exclude={"kasa_mgr"})
        assert driver_registry.has_driver("kasa_plug")
