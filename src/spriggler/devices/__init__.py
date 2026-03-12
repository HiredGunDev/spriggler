"""Device driver registry and base class.

Device drivers send commands to actuators.  They don't query state —
state verification comes from sensors and power monitoring.

Every device has discrete states.  Binary devices have ["off", "on"].
Graduated devices have ["off", "low", "high"] or similar.  The
controller calls set_state() with one of the declared states.

Drivers self-register when their module is imported.  Autodiscovery
imports all modules in this package at startup.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Type

log = logging.getLogger("spriggler.devices")


class DeviceCommandError(Exception):
    """Raised when a device command fails due to hardware/network issues."""
    pass


class DeviceDriver(ABC):
    """Base class for all device drivers.

    Contract:
    - set_state() sends a command and returns True/False for success.
      Success means "command sent," not "device confirmed."  Sensor
      feedback is the source of truth.
    - get_states() returns the ordered list of available states.
      First state is always "off."
    - last_commanded_state tracks what we last told the device to do.
    - Drivers must handle network/hardware errors gracefully.
    - No method should block indefinitely.
    """

    @abstractmethod
    def __init__(self, device_name: str, driver_config: dict) -> None:
        """Initialize the driver.

        Parameters
        ----------
        device_name : str
            Name from config (for logging).
        driver_config : dict
            The driver_config block from config.

        Raises
        ------
        ValueError
            If driver_config is missing required fields.
        """
        pass

    @abstractmethod
    def set_state(self, state: str) -> bool:
        """Command the device to a specific state.

        Parameters
        ----------
        state : str
            One of the strings from get_states().

        Returns
        -------
        bool
            True if the command was sent successfully.
            Does NOT mean the device confirmed state change.

        Raises
        ------
        ValueError
            If state is not in get_states().
        DeviceCommandError
            On hardware/network failure.
        """
        pass

    def get_states(self) -> list[str]:
        """Return ordered list of available states.

        Default: ["off", "on"] for binary devices.
        Override for graduated devices.
        First state must be "off".  States ordered low→high output.
        """
        return ["off", "on"]

    @property
    def last_commanded_state(self) -> str:
        """The last state we told the device to enter.

        This is what we COMMANDED, not necessarily what the device
        is doing.  Sensor feedback verifies actual state.
        """
        return getattr(self, "_last_commanded_state", "off")

    def supports_countdown(self) -> bool:
        """Whether this device supports hardware countdown timers.

        KASA plugs can autonomously turn off after a countdown,
        providing hardware-level safety independent of the daemon.
        """
        return False

    def set_countdown(self, seconds: int, target_state: str = "off") -> bool:
        """Set a hardware countdown timer.

        The device will autonomously switch to target_state after
        the specified seconds.  The daemon refreshes this each cycle.
        If the daemon dies, the hardware enforces safe state.

        Returns True if set successfully.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support countdown timers"
        )

    @abstractmethod
    def validate_config(self, driver_config: dict) -> None:
        """Validate driver-specific configuration.

        Called during config validation.  Checks syntax, not
        hardware reachability.
        """
        pass

    @property
    @abstractmethod
    def driver_name(self) -> str:
        """Driver's registered name (e.g., 'kasa_plug')."""
        pass

    @property
    def device_name(self) -> str:
        """Device name from config."""
        return getattr(self, "_device_name", "unknown")


# ── Device driver registry ───────────────────────────────────────

class DeviceDriverRegistry:
    """Registry of available device driver classes."""

    def __init__(self) -> None:
        self._drivers: dict[str, Type[DeviceDriver]] = {}

    def register(self, name: str, driver_cls: Type[DeviceDriver]) -> None:
        self._drivers[name] = driver_cls
        log.debug("Registered device driver: %s", name)

    def get(self, name: str) -> Type[DeviceDriver] | None:
        return self._drivers.get(name)

    def has_driver(self, name: str) -> bool:
        return name in self._drivers

    def list_drivers(self) -> dict[str, Type[DeviceDriver]]:
        return dict(self._drivers)


# Global registry — populated by autodiscovery at startup.
driver_registry = DeviceDriverRegistry()
