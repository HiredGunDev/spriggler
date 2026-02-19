"""Test the conformance test harnesses themselves using mock drivers.

These mock drivers aren't real hardware drivers. They exist to prove
that the conformance harness catches violations and passes compliant
drivers. When real drivers are implemented, they get their own test
files that subclass the same harnesses.
"""

import re
import pytest

from spriggler.sensors.base import SensorDriver, SensorReadError
from spriggler.devices.base import DeviceDriver, DeviceCommandError
from tests.conformance import SensorConformanceTests, DeviceConformanceTests


# ── Mock sensor driver (compliant) ───────────────────────────────────────────

class MockSensor(SensorDriver):
    """A fake sensor that returns canned data. Fully compliant."""

    def __init__(self, driver_config: dict) -> None:
        self._config = driver_config

    def read(self) -> dict | None:
        return {
            "temperature": 295.37,
            "humidity": 63.2,
            "battery": 87,
            "signal_strength": -72
        }

    def validate_config(self, driver_config: dict) -> None:
        if 'address' not in driver_config:
            raise ValueError("Missing required field: 'address'")
        addr = driver_config['address']
        if not re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', addr):
            raise ValueError(f"Invalid MAC address: '{addr}'")

    @property
    def driver_name(self) -> str:
        return "mock_sensor"


# ── Mock device driver (compliant, with countdown) ──────────────────────────

class MockDevice(DeviceDriver):
    """A fake device that tracks state. Fully compliant."""

    def __init__(self, driver_config: dict) -> None:
        self._config = driver_config
        self._on = False
        self._countdown = None

    def turn_on(self) -> bool:
        self._on = True
        return True

    def turn_off(self) -> bool:
        self._on = False
        return True

    def is_on(self) -> bool:
        return self._on

    def get_power(self) -> float | None:
        return 1500.0 if self._on else 0.0

    def supports_countdown(self) -> bool:
        return True

    def set_countdown(self, seconds: int, target_state: str = 'off') -> bool:
        self._countdown = (seconds, target_state)
        return True

    def validate_config(self, driver_config: dict) -> None:
        if 'address' not in driver_config:
            raise ValueError("Missing required field: 'address'")

    @property
    def driver_name(self) -> str:
        return "mock_device"


# ── Conformance tests for the mock sensor ────────────────────────────────────

class TestMockSensorConformance(SensorConformanceTests):

    @pytest.fixture
    def driver(self):
        return MockSensor({"address": "AA:BB:CC:DD:EE:FF"})

    @pytest.fixture
    def sample_reading(self):
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


# ── Conformance tests for the mock device ────────────────────────────────────

class TestMockDeviceConformance(DeviceConformanceTests):

    @pytest.fixture
    def driver(self):
        return MockDevice({"address": "192.168.1.100"})

    @pytest.fixture
    def driver_config_valid(self):
        return {"address": "192.168.1.100"}

    @pytest.fixture
    def driver_config_invalid(self):
        return {}  # Missing 'address'

    @pytest.fixture
    def has_countdown(self):
        return True

    @pytest.fixture
    def has_power_monitoring(self):
        return True
