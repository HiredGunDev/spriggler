"""Tests for VeSync humidifier device driver."""

import os
import pytest
from unittest.mock import patch

from spriggler.devices.vesync_device import VeSyncHumidifier
from spriggler.devices.base import DeviceCommandError
from tests.mock_vesync import MockVeSyncManager


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_mgr():
    """Create a mock VeSync manager with one humidifier."""
    mgr = MockVeSyncManager()
    mgr.add_humidifier('Dual 200S')
    return mgr


@pytest.fixture
def driver(mock_mgr):
    """Create a VeSyncHumidifier driver wired to the mock manager."""
    cfg = {
        'name': 'Dual 200S',
        'email': 'test@test.com',
        'password': 'testpass',
    }
    d = VeSyncHumidifier(cfg)
    d._mgr = mock_mgr
    d._device = mock_mgr.get_humidifier('Dual 200S')
    return d


@pytest.fixture
def graduated_driver(mock_mgr):
    """Driver with custom 4-state mapping (simulates a 300S with 9 levels)."""
    cfg = {
        'name': 'Dual 200S',
        'email': 'test@test.com',
        'password': 'testpass',
        'states': {'low': 1, 'mid': 5, 'high': 9},
    }
    d = VeSyncHumidifier(cfg)
    d._mgr = mock_mgr
    d._device = mock_mgr.get_humidifier('Dual 200S')
    return d


# ── State enumeration ────────────────────────────────────────────────


class TestStates:
    def test_default_states(self, driver):
        """Dual 200S default: off, low (level 1), high (level 2)."""
        assert driver.get_available_states() == ['off', 'low', 'high']

    def test_default_levels(self, driver):
        """Default low=1, high=2 for Dual 200S."""
        assert driver._state_levels == {'low': 1, 'high': 2}

    def test_custom_states(self, graduated_driver):
        states = graduated_driver.get_available_states()
        assert states == ['off', 'low', 'mid', 'high']

    def test_states_ordered_by_level(self):
        """States are sorted by mist level, not name."""
        cfg = {
            'name': 'Dual 200S',
            'email': 'test@test.com',
            'password': 'testpass',
            'states': {'high': 9, 'low': 1, 'medium': 5},
        }
        d = VeSyncHumidifier(cfg)
        assert d.get_available_states() == ['off', 'low', 'medium', 'high']


# ── Turn on/off ──────────────────────────────────────────────────────


class TestOnOff:
    def test_turn_on(self, driver):
        """turn_on() goes to highest state (high)."""
        assert driver.turn_on()
        assert driver._last_known_state == 'high'

    def test_turn_off(self, driver):
        driver.turn_on()
        assert driver.turn_off()
        assert driver._last_known_state == 'off'

    def test_is_on_when_off(self, driver):
        assert not driver.is_on()

    def test_is_on_when_on(self, driver):
        driver.turn_on()
        assert driver.is_on()


# ── Set state ────────────────────────────────────────────────────────


class TestSetState:
    def test_set_off(self, driver):
        driver.turn_on()
        assert driver.set_state('off')
        assert driver._last_known_state == 'off'

    def test_set_low(self, driver):
        assert driver.set_state('low')
        assert driver._last_known_state == 'low'
        assert driver._device._mist_level == 1

    def test_set_high(self, driver):
        """High is level 2 on Dual 200S."""
        assert driver.set_state('high')
        assert driver._last_known_state == 'high'
        assert driver._device._mist_level == 2

    def test_set_invalid_state(self, driver):
        with pytest.raises(ValueError, match="Invalid state"):
            driver.set_state('turbo')

    def test_set_mid_graduated(self, graduated_driver):
        assert graduated_driver.set_state('mid')
        assert graduated_driver._last_known_state == 'mid'
        assert graduated_driver._device._mist_level == 5

    def test_set_state_turns_on_device(self, driver):
        """Setting any non-off state should turn the device on first."""
        assert driver._device.device_status == 'off'
        driver.set_state('low')
        assert driver._device.device_status == 'on'


# ── Get current state ────────────────────────────────────────────────


class TestGetCurrentState:
    def test_off_when_device_off(self, driver):
        assert driver.get_current_state() == 'off'

    def test_returns_low(self, driver):
        driver.set_state('low')
        assert driver.get_current_state() == 'low'

    def test_returns_high(self, driver):
        driver.set_state('high')
        assert driver.get_current_state() == 'high'

    def test_closest_state_mapping(self, driver):
        """With default {low: 1, high: 2}, level 2 maps to high."""
        driver._device.device_status = 'on'
        driver._device._mist_level = 2
        assert driver.get_current_state() == 'high'

    def test_closest_state_mid(self, graduated_driver):
        """With 3 states, level 5 maps to mid."""
        graduated_driver._device.device_status = 'on'
        graduated_driver._device._mist_level = 5
        assert graduated_driver.get_current_state() == 'mid'


# ── Power ────────────────────────────────────────────────────────────


class TestPower:
    def test_no_power_monitoring(self, driver):
        assert driver.get_power() is None


# ── Config validation ────────────────────────────────────────────────


class TestValidation:
    def test_missing_name(self):
        with pytest.raises(ValueError, match="name"):
            VeSyncHumidifier({
                'email': 'a@b.com', 'password': 'x'
            })

    def test_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="credentials"):
                VeSyncHumidifier({'name': 'Test'})

    def test_env_credentials(self):
        """Credentials from environment variables."""
        with patch.dict(os.environ, {
            'VESYNC_EMAIL': 'env@test.com',
            'VESYNC_PASSWORD': 'envpass',
        }):
            d = VeSyncHumidifier({'name': 'Test'})
            assert d._email == 'env@test.com'
            assert d._password == 'envpass'

    def test_off_in_states_rejected(self):
        with pytest.raises(ValueError, match="off.*reserved"):
            VeSyncHumidifier({
                'name': 'Test',
                'email': 'a@b.com',
                'password': 'x',
                'states': {'off': 0, 'low': 1},
            })

    def test_invalid_level_zero(self):
        with pytest.raises(ValueError, match="positive"):
            VeSyncHumidifier({
                'name': 'Test',
                'email': 'a@b.com',
                'password': 'x',
                'states': {'low': 0},
            })

    def test_invalid_level_negative(self):
        with pytest.raises(ValueError, match="positive"):
            VeSyncHumidifier({
                'name': 'Test',
                'email': 'a@b.com',
                'password': 'x',
                'states': {'low': -1},
            })

    def test_high_level_accepted(self):
        """Levels above 9 should be accepted (device validates)."""
        d = VeSyncHumidifier({
            'name': 'Test',
            'email': 'a@b.com',
            'password': 'x',
            'states': {'blast': 10},
        })
        assert d._state_levels['blast'] == 10

    def test_states_not_dict(self):
        with pytest.raises(ValueError, match="dict"):
            VeSyncHumidifier({
                'name': 'Test',
                'email': 'a@b.com',
                'password': 'x',
                'states': ['low', 'high'],
            })


# ── Driver name / registry ───────────────────────────────────────────


class TestRegistry:
    def test_driver_name(self, driver):
        assert driver.driver_name == 'vesync_humidifier'

    def test_registered(self):
        from spriggler.devices.registry import DEVICE_DRIVERS
        assert 'vesync_humidifier' in DEVICE_DRIVERS

    def test_registry_returns_class(self):
        from spriggler.devices.registry import get_device_driver
        cls = get_device_driver('vesync_humidifier')
        assert cls is VeSyncHumidifier