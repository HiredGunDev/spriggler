"""Abstract base class for sensor drivers.

Every sensor driver must subclass SensorDriver and implement the
required methods. The conformance test harness validates compliance.

Contract:
    - read() returns a dict with string keys and numeric values
    - All keys must be in the standard taxonomy
    - All values must be in SI units (Kelvin for temperature, %RH for humidity)
    - read() must not block indefinitely
    - read() must not crash on hardware errors; return None or raise SensorReadError
"""

from abc import ABC, abstractmethod


class SensorReadError(Exception):
    """Raised when a sensor read fails due to hardware/network issues."""
    pass


class SensorDriver(ABC):
    """Base class for all sensor drivers."""

    # Standard key taxonomy. Drivers must only return keys from this set.
    VALID_KEYS = {
        'temperature',       # Kelvin
        'humidity',          # %RH (0-100)
        'battery',           # Percent (0-100)
        'signal_strength',   # dBm (negative)
        'co2',               # ppm
        'ph',                # dimensionless (0-14)
        'ec',                # electrical conductivity, mS/cm
        'light',             # lux
        'pressure',          # Pascals
        'soil_moisture',     # %
        'water_temperature', # Kelvin
    }

    @abstractmethod
    def __init__(self, driver_config: dict) -> None:
        """Initialize the driver with its configuration.

        Args:
            driver_config: The driver_config block from the sensor's
                           config entry. Contents are driver-specific.

        Raises:
            ValueError: If driver_config is missing required fields
                        or contains invalid values.
        """
        pass

    @abstractmethod
    def read(self) -> dict | None:
        """Read current values from the sensor.

        Returns:
            A dict mapping standard taxonomy keys to SI-unit values,
            or None if no data is available (e.g., BLE advertisement
            not received within timeout).

            Example:
                {
                    "temperature": 295.37,   # Kelvin
                    "humidity": 63.2,        # %RH
                    "battery": 87,           # percent
                    "signal_strength": -72   # dBm
                }

        Raises:
            SensorReadError: If a hardware/network error occurs that
                             prevents reading. The caller handles this
                             gracefully (marks sensor as missed poll).
        """
        pass

    @abstractmethod
    def validate_config(self, driver_config: dict) -> None:
        """Validate driver-specific configuration.

        Called during config validation phase, before the daemon starts.
        Checks that all required fields are present and values are
        syntactically valid (e.g., MAC address format). Does NOT check
        hardware reachability.

        Args:
            driver_config: The driver_config block to validate.

        Raises:
            ValueError: If configuration is invalid, with a message
                        describing what's wrong.
        """
        pass

    @property
    @abstractmethod
    def driver_name(self) -> str:
        """Return the driver's registered name (e.g., 'govee_h5100')."""
        pass

