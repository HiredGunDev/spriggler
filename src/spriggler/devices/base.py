"""Abstract base class for device drivers.

Every device driver must subclass DeviceDriver and implement the
required methods. The conformance test harness validates compliance.

Contract:
    - turn_on() and turn_off() return True on success, False on failure
    - is_on() returns a boolean reflecting last known state
    - get_power() returns watts as a float, or None if not supported
    - Graduated control is opt-in via get_available_states()
    - Countdown support is opt-in via supports_countdown()
    - All methods must handle network/hardware errors gracefully
    - No method should block indefinitely
"""

from abc import ABC, abstractmethod


class DeviceCommandError(Exception):
    """Raised when a device command fails due to hardware/network issues."""
    pass


class DeviceDriver(ABC):
    """Base class for all device drivers.

    Devices have discrete states. The simplest device has two states:
    'off' and 'on'. A graduated device might have ['off', 'low', 'mid', 'high']
    or ['off', '500W', '800W', '1000W', '1500W'].

    The solver enumerates feasible combinations of device states across
    all devices. Each state has a calibrated energy contribution learned
    during calibration. The solver picks the combination with lowest cost.

    State naming convention:
        - 'off' is always the first state (index 0)
        - 'on' is always the last state (max output)
        - Intermediate states are ordered from lowest to highest output
        - State names are strings, chosen by the driver
        - The solver doesn't interpret names — it uses calibrated values
    """

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

    def get_available_states(self) -> list[str]:
        """Return the ordered list of states this device supports.

        Default is ['off', 'on'] for simple binary devices. Override
        for graduated devices.

        States must be ordered from lowest output to highest output.
        The first state must be 'off'. The last state should be the
        maximum output state.

        Examples:
            Simple plug:       ['off', 'on']
            VeSync humidifier: ['off', 'low', 'mid', 'high']
            Smart heater:      ['off', '500W', '800W', '1000W', '1500W']
            Damper servo:      ['off', '25%', '50%', '75%', '100%']

        Returns:
            List of state name strings, ordered low to high.
        """
        return ['off', 'on']

    def set_state(self, state: str) -> bool:
        """Set the device to a specific state.

        For binary devices, this is equivalent to turn_on()/turn_off().
        For graduated devices, this sets the specific output level.

        The default implementation maps 'off' to turn_off() and
        anything else to turn_on(). Graduated drivers must override
        this to handle intermediate states.

        Args:
            state: One of the strings from get_available_states().

        Returns:
            True if the command was sent successfully, False otherwise.

        Raises:
            ValueError: If state is not in get_available_states().
            DeviceCommandError: On hardware/network failure.
        """
        available = self.get_available_states()
        if state not in available:
            raise ValueError(
                f"Invalid state '{state}' for {self.driver_name}. "
                f"Available: {available}"
            )
        if state == 'off':
            return self.turn_off()
        else:
            return self.turn_on()

    def get_current_state(self) -> str:
        """Return the current state of the device.

        For binary devices, returns 'off' or 'on' based on is_on().
        Graduated drivers should override to return the specific level.

        Returns:
            One of the strings from get_available_states().
        """
        return 'on' if self.is_on() else 'off'

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

