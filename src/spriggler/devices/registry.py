"""Device registry - maps driver names from config to device classes."""

from spriggler.devices.mock import MockDevice

DEVICE_DRIVERS: dict[str, type] = {
    'mock': MockDevice,
}


def get_device_driver(name: str) -> type:
    """Look up a device driver class by name."""
    if name not in DEVICE_DRIVERS:
        available = ', '.join(sorted(DEVICE_DRIVERS.keys()))
        raise KeyError(
            f"Unknown device driver: '{name}'. Available: {available}"
        )
    return DEVICE_DRIVERS[name]