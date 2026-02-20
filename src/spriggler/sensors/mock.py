"""Mock sensor for testing and demonstration.

Returns simulated data that drifts randomly around a configurable
center point. Used by the daemon in demo mode, conformance tests,
and integration tests.
"""

import random
import re

from spriggler.sensors.base import SensorDriver


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
        if random.random() < self._drop_rate:
            return None

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
    