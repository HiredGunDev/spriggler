"""Tests for power sensor interface and mock implementation."""

import pytest
from spriggler.devices.power import PowerSensor, PowerSensorError
from spriggler.devices.mock_power import MockPowerSensor


class TestPowerSensorABC:
    """Verify the PowerSensor abstract interface."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            PowerSensor({})

    def test_error_class_exists(self):
        assert issubclass(PowerSensorError, Exception)


class TestMockPowerSensor:
    """Test MockPowerSensor implementation."""

    def _make(self, **kwargs) -> MockPowerSensor:
        cfg = {'strip': 'Test Strip', 'plug': 'Test Plug'}
        cfg.update(kwargs)
        return MockPowerSensor(cfg)

    def test_read_power_default(self):
        ps = self._make()
        assert ps.read_power() == 0.0

    def test_read_power_configured(self):
        ps = self._make(watts=45.2)
        assert ps.read_power() == 45.2

    def test_set_watts(self):
        ps = self._make()
        ps.set_watts(100.0)
        assert ps.read_power() == 100.0

    def test_driver_name(self):
        ps = self._make()
        assert ps.driver_name == 'mock_power'

    # ── Cutoff ───────────────────────────────────────────────────

    def test_supports_cutoff(self):
        ps = self._make()
        assert ps.supports_cutoff() is True

    def test_cut_power(self):
        ps = self._make(watts=100.0)
        assert ps.cut_power() is True
        assert ps.is_cut is True
        assert ps.read_power() == 0.0

    def test_restore_power(self):
        ps = self._make(watts=100.0)
        ps.cut_power()
        assert ps.restore_power() is True
        assert ps.is_cut is False
        assert ps.read_power() == 100.0

    def test_cutoff_disabled(self):
        ps = self._make(has_cutoff=False)
        assert ps.supports_cutoff() is False
        with pytest.raises(NotImplementedError):
            ps.cut_power()

    # ── Countdown ────────────────────────────────────────────────

    def test_supports_countdown(self):
        ps = self._make()
        assert ps.supports_countdown() is True

    def test_set_countdown(self):
        ps = self._make()
        assert ps.set_countdown(300, 'off') is True
        assert ps.last_countdown == (300, 'off')

    def test_set_countdown_on(self):
        ps = self._make()
        ps.set_countdown(60, 'on')
        assert ps.last_countdown == (60, 'on')

    def test_countdown_disabled(self):
        ps = self._make(has_countdown=False)
        assert ps.supports_countdown() is False
        with pytest.raises(NotImplementedError):
            ps.set_countdown(300)

    # ── Validation ───────────────────────────────────────────────

    def test_validate_config_valid(self):
        ps = self._make()
        ps.validate_config({'strip': 'X', 'plug': 'Y'})

    def test_validate_config_missing_strip(self):
        ps = self._make()
        with pytest.raises(ValueError, match="strip"):
            ps.validate_config({'plug': 'Y'})

    def test_validate_config_missing_plug(self):
        ps = self._make()
        with pytest.raises(ValueError, match="plug"):
            ps.validate_config({'strip': 'X'})
