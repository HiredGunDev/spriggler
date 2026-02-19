"""Abstract base class for device drivers.

Every device driver must subclass DeviceDriver and implement the
required methods. The conformance test harness validates compliance.

Contract:
    - turn_on() and turn_off() return True on success, False on failure
    - is_on() returns a boolean reflecting last known state
    - get_power() returns watts as a float, or None if not supported
    - Countdown support is opt-in via supports_countdown()
    - All methods must handle network/hardware errors gracefully
    - No method should block indefinitely
"""

from abc import ABC, abstractmethod


class DeviceCommandError(Exception):
    """Raised when a device command fails due to hardware/network issues."""
    pass


class DeviceDriver(ABC):
    """Base class for all device drivers."""

    @abstractmethod
    def __init__(self, driver_config: dict) -> None:
        """Initialize the driver with its configuration.

        Args:
            driver_config: The driver_config block from the device's
                           config entry. Contents are driver-specific.

        Raises:
            ValueError: If driver_config is missing required fields
                        or contains invalid values.
        """
        pass

    @abstractmethod
    def turn_on(self) -> bool:
        """Turn the device on.

        Returns:
            True if the command was sent successfully, False otherwise.
            Note: success means the command was sent, not that the
            device has confirmed state change. Sensor feedback is
            the source of truth.

        Raises:
            DeviceCommandError: On hardware/network failure.
        """
        pass

    @abstractmethod
    def turn_off(self) -> bool:
        """Turn the device off.

        Returns:
            True if the command was sent successfully, False otherwise.

        Raises:
            DeviceCommandError: On hardware/network failure.
        """
        pass

    @abstractmethod
    def is_on(self) -> bool:
        """Return the last known state of the device.

        This is advisory only. The safety monitor and solver use
        sensor readings as the source of truth, not device self-report.

        Returns:
            True if the device is believed to be on, False otherwise.
        """
        pass

    @abstractmethod
    def get_power(self) -> float | None:
        """Return current power consumption in watts.

        Returns:
            Power in watts as a float, or None if this device does
            not support power monitoring.

        Raises:
            DeviceCommandError: On hardware/network failure.
        """
        pass

    def supports_countdown(self) -> bool:
        """Return whether this device supports hardware countdown timers.

        Default is False. Override to True for devices like KASA plugs
        that can autonomously turn off after a countdown, providing
        hardware-level safety independent of the daemon.

        Returns:
            True if set_countdown() is implemented and functional.
        """
        return False

    def set_countdown(self, seconds: int, target_state: str = 'off') -> bool:
        """Set a hardware countdown timer on the device.

        The device will autonomously switch to target_state after
        the specified number of seconds. The daemon refreshes this
        on every control cycle. If the daemon dies, the device
        enforces safe state on its own.

        Args:
            seconds: Countdown duration in seconds.
            target_state: State to enter when countdown expires.
                          Must be 'on' or 'off'.

        Returns:
            True if the countdown was set successfully, False otherwise.

        Raises:
            NotImplementedError: If supports_countdown() is False.
            DeviceCommandError: On hardware/network failure.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support countdown timers"
        )

    @abstractmethod
    def validate_config(self, driver_config: dict) -> None:
        """Validate driver-specific configuration.

        Called during config validation phase, before the daemon starts.
        Checks that all required fields are present and values are
        syntactically valid. Does NOT check hardware reachability.

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
        """Return the driver's registered name (e.g., 'kasa_plug')."""
        pass

