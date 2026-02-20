"""Mock drivers for testing and demonstration.

These drivers return simulated data. MockSensor returns values that
drift randomly around a configurable center point to simulate a real
environment. MockDevice tracks state in memory.

Used by:
    - The daemon in demo mode (no real hardware)
    - The conformance test harness
    - Integration tests
"""

import random
import re

from spriggler.sensors.base import SensorDriver
from spriggler.devices.base import DeviceDriver


class MockSensor(SensorDriver):
    """Simulated sensor with configurable drift.

    driver_config options:
        address: (required for validate_config) fake MAC address
        temperature: center temperature in Kelvin (default: 295.37 / ~72F)
        humidity: center humidity in %RH (default: 55.0)
        battery: battery percent (default: 87)
        signal_strength: RSSI in dBm (default: -72)
        drift: max random drift per read in Kelvin (default: 0.5)
        drop_rate: probability of returning None per read (default: 0.0)
    """

    def __init__(self, driver_config: dict) -> None:
        self._config = driver_config
        self._temp = driver_config.get('temperature', 295.37)
        self._humidity = driver_config.get('humidity', 55.0)
        self._battery = driver_config.get('battery', 87)
        self._rssi = driver_config.get('signal_strength', -72)
        self._drift = driver_config.get('drift', 0.5)
        self._drop_rate = driver_config.get('drop_rate', 0.0)

    def read(self) -> dict | None:
        # Simulate BLE drop
        if random.random() < self._drop_rate:
            return None

        # Drift temperature and humidity randomly
        self._temp += random.uniform(-self._drift, self._drift)
        self._humidity += random.uniform(-self._drift * 2, self._drift * 2)
        self._humidity = max(10, min(95, self._humidity))

        return {
            "temperature": round(self._temp, 2),
            "humidity": round(self._humidity, 1),
            "battery": self._battery,
            "signal_strength": self._rssi,
        }

    def validate_config(self, driver_config: dict) -> None:
        if 'address' not in driver_config:
            raise ValueError("Missing required field: 'address'")
        addr = driver_config['address']
        if not re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', addr):
            raise ValueError(f"Invalid MAC address: '{addr}'")

    @property
    def driver_name(self) -> str:
        return "mock"


class MockDevice(DeviceDriver):
    """Simulated device with configurable states.

    driver_config options:
        address: (required for validate_config) fake address
        states: list of state names (default: ['off', 'on'])
    """

    def __init__(self, driver_config: dict) -> None:
        self._config = driver_config
        self._states = driver_config.get('states', ['off', 'on'])
        self._state = 'off'
        self._countdown = None

    def turn_on(self) -> bool:
        self._state = self._states[-1]
        return True

    def turn_off(self) -> bool:
        self._state = 'off'
        return True

    def is_on(self) -> bool:
        return self._state != 'off'

    def get_available_states(self) -> list[str]:
        return list(self._states)

    def set_state(self, state: str) -> bool:
        if state not in self._states:
            raise ValueError(
                f"Invalid state '{state}'. Available: {self._states}"
            )
        self._state = state
        return True

    def get_current_state(self) -> str:
        return self._state

    def get_power(self) -> float | None:
        if self._state == 'off':
            return 0.0
        idx = self._states.index(self._state)
        return 1500.0 * idx / (len(self._states) - 1)

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
        return "mock"
