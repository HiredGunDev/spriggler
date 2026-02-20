"""Sensor registry - maps driver names from config to sensor classes."""

from spriggler.sensors.mock import MockSensor

SENSOR_DRIVERS: dict[str, type] = {
    'mock': MockSensor,
}

try:
    from spriggler.sensors.govee import GoveeSensor
    SENSOR_DRIVERS['govee_ble'] = GoveeSensor
except ImportError:
    pass


def get_sensor_driver(name: str) -> type:
    """Look up a sensor driver class by name."""
    if name not in SENSOR_DRIVERS:
        available = ', '.join(sorted(SENSOR_DRIVERS.keys()))
        raise KeyError(
            f"Unknown sensor driver: '{name}'. Available: {available}"
        )
    return SENSOR_DRIVERS[name]
