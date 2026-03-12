"""Sensor driver registry.

Sensor drivers self-register when their module is imported.
Autodiscovery imports all modules in this package at startup.

Usage:
    from spriggler.sensors import driver_registry

    # Get a driver class by name (from config)
    driver_cls = driver_registry.get("govee_ble")
    sensor = driver_cls(sensor_name="pod_sensor", driver_config={...})
    reading = sensor.read()
"""

from __future__ import annotations

import logging
from typing import Type

from spriggler.sensors.base import SensorDriver

log = logging.getLogger("spriggler.sensors")


class SensorDriverRegistry:
    """Registry of available sensor driver classes."""

    def __init__(self) -> None:
        self._drivers: dict[str, Type[SensorDriver]] = {}

    def register(self, name: str, driver_cls: Type[SensorDriver]) -> None:
        """Register a sensor driver class.

        Parameters
        ----------
        name : str
            Driver name as it appears in config (e.g., "govee_ble").
        driver_cls : Type[SensorDriver]
            The driver class (not an instance).
        """
        self._drivers[name] = driver_cls
        log.debug("Registered sensor driver: %s", name)

    def get(self, name: str) -> Type[SensorDriver] | None:
        """Get a driver class by name."""
        return self._drivers.get(name)

    def has_driver(self, name: str) -> bool:
        """Check if a driver is registered."""
        return name in self._drivers

    def list_drivers(self) -> dict[str, Type[SensorDriver]]:
        """Return all registered drivers."""
        return dict(self._drivers)


# Global registry — populated by autodiscovery at startup.
driver_registry = SensorDriverRegistry()
