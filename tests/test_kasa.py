"""Tests for KASA drivers.

These tests verify the KASA device driver and power sensor against
mock KASA devices. Live hardware tests require actual KASA devices
on the network and are marked with @pytest.mark.hardware.

The KasaConnectionManager async bridge is tested with a mock that
bypasses real network discovery.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from spriggler.devices.kasa_device import KasaDevice
from spriggler.devices.kasa_power import KasaPowerSensor
from spriggler.devices.kasa import KasaConnectionManager, KasaError
from spriggler.devices.base import DeviceCommandError
from spriggler.devices.power import PowerSensorError


# ── Helpers ──────────────────────────────────────────────────────────


class MockKasaManager:
    """Fake KasaConnectionManager that doesn't touch the network."""

    def __init__(self):
        self._plugs = {}  # (strip, plug) -> state dict
        self._started = True

    def add_plug(self, strip: str, plug: str, *,
                 is_on: bool = False, power: float | None = None,
                 has_countdown: bool = True):
        self._plugs[(strip, plug)] = {
            'is_on': is_on,
            'power': power,
            'has_countdown': has_countdown,
            'countdown': None,
        }

    def get_plug(self, strip_name: str, plug_name: str):
        key = (strip_name, plug_name)
        if key not in self._plugs:
            raise KasaError(f"Not found: {strip_name}/{plug_name}")
        return key  # Use the tuple as a token

    def turn_on(self, plug):
        self._plugs[plug]['is_on'] = True

    def turn_off(self, plug):
        self._plugs[plug]['is_on'] = False

    def is_on(self, plug):
        if plug not in self._plugs:
            raise KasaError(f"Plug not found: {plug}")
        return self._plugs[plug]['is_on']

    def update_device(self, plug):
        if plug not in self._plugs:
            raise KasaError(f"Plug not found: {plug}")
        pass  # No-op for mock

    def read_power(self, plug):
        return self._plugs[plug]['power']

    def has_countdown(self, plug):
        return self._plugs[plug]['has_countdown']

    def set_countdown(self, plug, seconds, target_state='off'):
        if not self._plugs[plug]['has_countdown']:
            return False
        self._plugs[plug]['countdown'] = (seconds, target_state)
        return True

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def mock_mgr():
    """Create a mock KASA manager with a test strip."""
    mgr = MockKasaManager()
    mgr.add_plug('Shed Strip', 'Heater', power=1487.3, has_countdown=True)
    mgr.add_plug('Shed Strip', 'Light', power=450.0, has_countdown=True)
    mgr.add_plug('Shed Strip', 'Humidifier', power=45.0, has_countdown=True)
    return mgr


# ── KasaDevice tests ─────────────────────────────────────────────────


class TestKasaDeviceValidation:
    """Config validation for KasaDevice."""

    def test_valid_config(self):
        with patch('spriggler.devices.kasa_device.get_kasa_manager'):
            dev = KasaDevice({'strip': 'X', 'plug': 'Y'})
            assert dev.driver_name == 'kasa_plug'

    def test_missing_strip(self):
        with pytest.raises(ValueError, match="strip"):
            with patch('spriggler.devices.kasa_device.get_kasa_manager'):
                KasaDevice({'plug': 'Y'})

    def test_missing_plug(self):
        with pytest.raises(ValueError, match="plug"):
            with patch('spriggler.devices.kasa_device.get_kasa_manager'):
                KasaDevice({'strip': 'X'})


class TestKasaDeviceControl:
    """Control operations for KasaDevice with mock manager."""

    def _make(self, mgr, strip='Shed Strip', plug='Heater'):
        with patch('spriggler.devices.kasa_device.get_kasa_manager',
                   return_value=mgr):
            return KasaDevice({'strip': strip, 'plug': plug})

    def test_turn_on(self, mock_mgr):
        dev = self._make(mock_mgr)
        assert dev.turn_on() is True
        assert mock_mgr._plugs[('Shed Strip', 'Heater')]['is_on'] is True

    def test_turn_off(self, mock_mgr):
        dev = self._make(mock_mgr)
        dev.turn_on()
        assert dev.turn_off() is True
        assert mock_mgr._plugs[('Shed Strip', 'Heater')]['is_on'] is False

    def test_is_on(self, mock_mgr):
        dev = self._make(mock_mgr)
        assert dev.is_on() is False
        dev.turn_on()
        assert dev.is_on() is True

    def test_get_power(self, mock_mgr):
        dev = self._make(mock_mgr)
        assert dev.get_power() == 1487.3

    def test_get_current_state(self, mock_mgr):
        dev = self._make(mock_mgr)
        assert dev.get_current_state() == 'off'
        dev.turn_on()
        assert dev.get_current_state() == 'on'

    def test_set_state(self, mock_mgr):
        dev = self._make(mock_mgr)
        dev.set_state('on')
        assert mock_mgr._plugs[('Shed Strip', 'Heater')]['is_on'] is True
        dev.set_state('off')
        assert mock_mgr._plugs[('Shed Strip', 'Heater')]['is_on'] is False

    def test_available_states(self, mock_mgr):
        dev = self._make(mock_mgr)
        assert dev.get_available_states() == ['off', 'on']

    def test_supports_countdown(self, mock_mgr):
        dev = self._make(mock_mgr)
        assert dev.supports_countdown() is True

    def test_set_countdown(self, mock_mgr):
        dev = self._make(mock_mgr)
        assert dev.set_countdown(300, 'off') is True
        assert mock_mgr._plugs[('Shed Strip', 'Heater')]['countdown'] == (300, 'off')

    def test_plug_not_found(self, mock_mgr):
        dev = self._make(mock_mgr, plug='Nonexistent')
        # Should return False (error handled gracefully)
        assert dev.turn_on() is False

    def test_turn_on_failure_returns_last_known(self, mock_mgr):
        """When turn_on fails, is_on returns last known state."""
        dev = self._make(mock_mgr)
        dev.turn_on()
        # Now break the manager
        mock_mgr._plugs.pop(('Shed Strip', 'Heater'))
        # is_on should return last known (True)
        assert dev.is_on() is True


# ── KasaPowerSensor tests ────────────────────────────────────────────


class TestKasaPowerSensorValidation:
    """Config validation for KasaPowerSensor."""

    def test_valid_config(self):
        with patch('spriggler.devices.kasa_power.get_kasa_manager'):
            ps = KasaPowerSensor({'strip': 'X', 'plug': 'Y'})
            assert ps.driver_name == 'kasa_strip'

    def test_missing_strip(self):
        with pytest.raises(ValueError, match="strip"):
            with patch('spriggler.devices.kasa_power.get_kasa_manager'):
                KasaPowerSensor({'plug': 'Y'})

    def test_missing_plug(self):
        with pytest.raises(ValueError, match="plug"):
            with patch('spriggler.devices.kasa_power.get_kasa_manager'):
                KasaPowerSensor({'strip': 'X'})


class TestKasaPowerSensorReading:
    """Power monitoring via KasaPowerSensor with mock manager."""

    def _make(self, mgr, strip='Shed Strip', plug='Humidifier'):
        with patch('spriggler.devices.kasa_power.get_kasa_manager',
                   return_value=mgr):
            return KasaPowerSensor({'strip': strip, 'plug': plug})

    def test_read_power(self, mock_mgr):
        ps = self._make(mock_mgr)
        assert ps.read_power() == 45.0

    def test_read_power_not_found(self, mock_mgr):
        ps = self._make(mock_mgr, plug='Nonexistent')
        assert ps.read_power() is None

    def test_supports_cutoff(self, mock_mgr):
        ps = self._make(mock_mgr)
        assert ps.supports_cutoff() is True

    def test_cut_power(self, mock_mgr):
        ps = self._make(mock_mgr)
        mock_mgr._plugs[('Shed Strip', 'Humidifier')]['is_on'] = True
        assert ps.cut_power() is True
        assert mock_mgr._plugs[('Shed Strip', 'Humidifier')]['is_on'] is False

    def test_restore_power(self, mock_mgr):
        ps = self._make(mock_mgr)
        ps.cut_power()
        assert ps.restore_power() is True
        assert mock_mgr._plugs[('Shed Strip', 'Humidifier')]['is_on'] is True

    def test_supports_countdown(self, mock_mgr):
        ps = self._make(mock_mgr)
        assert ps.supports_countdown() is True

    def test_set_countdown(self, mock_mgr):
        ps = self._make(mock_mgr)
        assert ps.set_countdown(180, 'off') is True
        assert mock_mgr._plugs[('Shed Strip', 'Humidifier')]['countdown'] == (180, 'off')


# ── KasaConnectionManager unit tests ─────────────────────────────────


class TestKasaConnectionManager:
    """Unit tests for the connection manager (no network)."""

    def test_not_started_raises(self):
        mgr = KasaConnectionManager()
        # Don't start it
        with pytest.raises(KasaError, match="not running"):
            mgr._run_async(asyncio.sleep(0))

    def test_start_stop(self):
        mgr = KasaConnectionManager()
        mgr.start()
        assert mgr._started is True
        assert mgr._loop is not None
        assert mgr._loop.is_running()
        mgr.stop()
        assert mgr._started is False

    def test_double_start_is_safe(self):
        mgr = KasaConnectionManager()
        mgr.start()
        mgr.start()  # Should not raise
        assert mgr._started is True
        mgr.stop()

    def test_stop_without_start_is_safe(self):
        mgr = KasaConnectionManager()
        mgr.stop()  # Should not raise


# ── Power sensor registry tests ──────────────────────────────────────


class TestPowerSensorRegistry:
    """Test power sensor registry lookups."""

    def test_mock_power_registered(self):
        from spriggler.devices.power_registry import get_power_sensor_driver
        cls = get_power_sensor_driver('mock_power')
        from spriggler.devices.mock_power import MockPowerSensor
        assert cls is MockPowerSensor

    def test_kasa_strip_registered(self):
        from spriggler.devices.power_registry import get_power_sensor_driver
        cls = get_power_sensor_driver('kasa_strip')
        assert cls is KasaPowerSensor

    def test_unknown_raises(self):
        from spriggler.devices.power_registry import get_power_sensor_driver
        with pytest.raises(KeyError, match="Unknown"):
            get_power_sensor_driver('aardvark_pdu')


# ── Device registry tests ────────────────────────────────────────────


class TestDeviceRegistry:
    """Test device registry includes KASA."""

    def test_kasa_plug_registered(self):
        from spriggler.devices.registry import get_device_driver
        cls = get_device_driver('kasa_plug')
        assert cls is KasaDevice

    def test_mock_still_registered(self):
        from spriggler.devices.registry import get_device_driver
        from spriggler.devices.mock import MockDevice
        cls = get_device_driver('mock')
        assert cls is MockDevice
