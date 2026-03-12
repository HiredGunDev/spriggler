"""Abstract base class for sensor drivers.

Every sensor driver must subclass SensorDriver and implement the
required methods.

Contract:
    - read() returns a SensorReading (dataclass) or None
    - All property values are in SI fundamental units:
        temperature:       Kelvin
        absolute_humidity: g/m³
        battery:           percent (0-100)
        signal_strength:   dBm (negative)
    - Drivers convert from native sensor units at the driver boundary.
      Nothing above the driver ever sees °F, °C, or %RH.
    - Every reading carries a sample_time (wall-clock time of the
      physical measurement arrival, e.g., BLE advertisement timestamp).
    - read() must not block indefinitely.
    - read() must not crash on hardware errors; return None or raise
      SensorReadError.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class SensorReadError(Exception):
    """Raised when a sensor read fails due to hardware/network issues."""
    pass


@dataclass
class SensorReading:
    """A timestamped sensor reading in SI fundamental units.

    Every reading carries sample_time — the wall-clock time the
    physical measurement was received (e.g., BLE advertisement
    arrival).  The freshness system uses this to classify data age.

    Properties are stored as a dict of fundamental quantities.
    Standard keys:
        temperature:       Kelvin
        absolute_humidity: g/m³  (converted from %RH at driver boundary)
        battery:           percent (0-100)
        signal_strength:   dBm (negative)
        co2:               ppm
        ph:                dimensionless (0-14)
        ec:                mS/cm
        light:             lux
        pressure:          Pascals
        soil_moisture:     volumetric %
        water_temperature: Kelvin
        dissolved_oxygen:  mg/L
    """

    # Wall-clock time this reading was received by the driver
    sample_time: float

    # Property values in SI fundamental units
    values: dict[str, float] = field(default_factory=dict)

    @property
    def age(self) -> float:
        """Seconds since this reading was received."""
        return time.time() - self.sample_time

    def get(self, key: str, default: float | None = None) -> float | None:
        """Get a property value by key."""
        return self.values.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.values


# Standard property taxonomy — all in SI fundamental units.
# Drivers must only return keys from this set.
VALID_PROPERTIES = {
    "temperature",          # Kelvin
    "absolute_humidity",    # g/m³
    "battery",              # percent (0-100)
    "signal_strength",      # dBm (negative)
    "co2",                  # ppm
    "ph",                   # dimensionless (0-14)
    "ec",                   # mS/cm (electrical conductivity)
    "light",                # lux
    "pressure",             # Pascals
    "soil_moisture",        # volumetric percent
    "water_temperature",    # Kelvin
    "dissolved_oxygen",     # mg/L
}


class SensorDriver(ABC):
    """Base class for all sensor drivers.

    Subclasses must:
    1. Convert native sensor units to SI fundamentals in read().
    2. Include sample_time in every SensorReading.
    3. Register with the sensor driver registry at module level.
    """

    @abstractmethod
    def __init__(self, sensor_name: str, driver_config: dict) -> None:
        """Initialize the driver with its configuration.

        Parameters
        ----------
        sensor_name : str
            The sensor's name from config (for logging).
        driver_config : dict
            The driver_config block from the sensor's config entry.
            Contents are driver-specific.

        Raises
        ------
        ValueError
            If driver_config is missing required fields or contains
            invalid values.
        """
        pass

    @abstractmethod
    def read(self) -> SensorReading | None:
        """Read current values from the sensor.

        Returns
        -------
        SensorReading or None
            A reading with all values in SI fundamental units and
            a sample_time timestamp.  Returns None if no data is
            available (e.g., no BLE advertisement received yet).

        Raises
        ------
        SensorReadError
            If a hardware/network error prevents reading.
        """
        pass

    @abstractmethod
    def validate_config(self, driver_config: dict) -> None:
        """Validate driver-specific configuration.

        Called during config validation, before the daemon starts.
        Checks that all required fields are present and values are
        syntactically valid.  Does NOT check hardware reachability.

        Raises
        ------
        ValueError
            If configuration is invalid.
        """
        pass

    @property
    @abstractmethod
    def driver_name(self) -> str:
        """Return the driver's registered name (e.g., 'govee_ble')."""
        pass
