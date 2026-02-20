"""Driver registry - maps driver names from config to driver classes.

The config specifies drivers by name (e.g., "mock", "govee_ble", "kasa").
This module resolves those names to actual classes.
"""

from spriggler.drivers.mock import MockSensor, MockDevice


# Registry of known drivers. Add real drivers here as they're implemented.
SENSOR_DRIVERS: dict[str, type] = {
    'mock': MockSensor,
}

DEVICE_DRIVERS: dict[str, type] = {
    'mock': MockDevice,
}


def get_sensor_driver(name: str) -> type:
    """Look up a sensor driver class by name.

    Raises KeyError with a helpful message if not found.
    """
    if name not in SENSOR_DRIVERS:
        available = ', '.join(sorted(SENSOR_DRIVERS.keys()))
        raise KeyError(
            f"Unknown sensor driver: '{name}'. "
            f"Available: {available}"
        )
    return SENSOR_DRIVERS[name]


def get_device_driver(name: str) -> type:
    """Look up a device driver class by name.

    Raises KeyError with a helpful message if not found.
    """
    if name not in DEVICE_DRIVERS:
        available = ', '.join(sorted(DEVICE_DRIVERS.keys()))
        raise KeyError(
            f"Unknown device driver: '{name}'. "
            f"Available: {available}"
        )
    return DEVICE_DRIVERS[name]
