"""Power sensor registry - maps driver names to power sensor classes."""

from spriggler.devices.mock_power import MockPowerSensor

POWER_SENSOR_DRIVERS: dict[str, type] = {
    'mock_power': MockPowerSensor,
}

try:
    from spriggler.devices.kasa_power import KasaPowerSensor
    POWER_SENSOR_DRIVERS['kasa_strip'] = KasaPowerSensor
except ImportError:
    pass


def get_power_sensor_driver(name: str) -> type:
    """Look up a power sensor driver class by name."""
    if name not in POWER_SENSOR_DRIVERS:
        available = ', '.join(sorted(POWER_SENSOR_DRIVERS.keys()))
        raise KeyError(
            f"Unknown power sensor driver: '{name}'. Available: {available}"
        )
    return POWER_SENSOR_DRIVERS[name]
