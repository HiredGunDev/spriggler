"""Abstract base class for power sensors.

A power sensor monitors the electrical consumption of a device and
optionally provides hardware-level safety capabilities (power cutoff,
countdown timers).

Power sensors are NOT device drivers. They don't control what a device
does — they monitor and can kill the power to a device as a safety
measure. A VeSync humidifier is controlled by its own device driver.
The KASA strip it's plugged into monitors its power and can cut it
off in an emergency.

Contract:
    - read_power() returns watts as a float, or None if unavailable
    - supports_cutoff() and cut_power() provide emergency kill
    - supports_countdown() and set_countdown() provide hardware failsafe
    - All methods must handle network/hardware errors gracefully
    - No method should block indefinitely
"""

from abc import ABC, abstractmethod


class PowerSensorError(Exception):
    """Raised when a power sensor operation fails."""
    pass


class PowerSensor(ABC):
    """Base class for power monitoring and hardware failsafe drivers.

    A power sensor answers three questions:
        1. How many watts is this device drawing right now?
        2. Can I kill power to this device in an emergency?
        3. Can I set a hardware countdown timer on this device?

    Not all power sensors support all capabilities. A CT clamp on
    a Shelly reads watts but can't cut power. A KASA strip can do
    all three. The daemon queries capabilities and uses what's available.
    """

    @abstractmethod
    def __init__(self, driver_config: dict) -> None:
        """Initialize the power sensor driver.

        Args:
            driver_config: The power_sensor block from the device's
                           config entry (minus the 'driver' key).

        Raises:
            ValueError: If config is missing required fields.
        """
        pass

    @abstractmethod
    def read_power(self) -> float | None:
        """Read current power consumption in watts.

        Returns:
            Power in watts as a float, or None if the reading
            is unavailable (device off, communication error, etc).

        Raises:
            PowerSensorError: On hardware/network failure.
        """
        pass

    def supports_cutoff(self) -> bool:
        """Return whether this sensor can cut power to the device.

        Default is False. Override to True for smart plugs/strips
        that can kill power to a specific outlet.
        """
        return False

    def cut_power(self) -> bool:
        """Emergency power cutoff.

        Immediately kills power to the monitored device. This is a
        safety action — the safety monitor calls this when the device
        driver is unresponsive and the device is causing harm.

        Returns:
            True if power was cut successfully, False otherwise.

        Raises:
            NotImplementedError: If supports_cutoff() is False.
            PowerSensorError: On hardware/network failure.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support power cutoff"
        )

    def restore_power(self) -> bool:
        """Restore power after a cutoff.

        Re-enables the outlet. The device driver then handles
        returning the device to its commanded state.

        Returns:
            True if power was restored successfully.

        Raises:
            NotImplementedError: If supports_cutoff() is False.
            PowerSensorError: On hardware/network failure.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support power cutoff"
        )

    def supports_countdown(self) -> bool:
        """Return whether this sensor supports hardware countdown timers.

        Default is False. Override for devices like KASA plugs that
        can autonomously cut power after a countdown.
        """
        return False

    def set_countdown(self, seconds: int, target_state: str = 'off') -> bool:
        """Set a hardware countdown timer.

        The outlet will autonomously switch to target_state after
        the specified seconds. The daemon refreshes this each cycle.
        If the daemon dies, the hardware enforces the safe state.

        Args:
            seconds: Countdown duration in seconds.
            target_state: 'on' or 'off'.

        Returns:
            True if the countdown was set successfully.

        Raises:
            NotImplementedError: If supports_countdown() is False.
            PowerSensorError: On hardware/network failure.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support countdown timers"
        )

    @abstractmethod
    def validate_config(self, driver_config: dict) -> None:
        """Validate power sensor configuration.

        Args:
            driver_config: The config block to validate.

        Raises:
            ValueError: If configuration is invalid.
        """
        pass

    @property
    @abstractmethod
    def driver_name(self) -> str:
        """Return the driver's registered name (e.g., 'kasa_strip')."""
        pass
