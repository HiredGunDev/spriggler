"""Mock device for testing and demonstration.

Tracks state in memory. Used by the daemon in demo mode,
conformance tests, and integration tests.
"""

from spriggler.devices.base import DeviceDriver


class MockDevice(DeviceDriver):
    """Simulated device with configurable states.

    driver_config options:
        address: (required for validate_config) fake address
        states: list of state names (default: ['off', 'on'])
    """

    def __init__(self, driver_config: dict) -> None:
        self._config = driver_config
        self._states = driver_config.get('states', ['off', 'on'])
        self._state = 'off'
        self._countdown = None

    def turn_on(self) -> bool:
        self._state = self._states[-1]
        return True

    def turn_off(self) -> bool:
        self._state = 'off'
        return True

    def is_on(self) -> bool:
        return self._state != 'off'

    def get_available_states(self) -> list[str]:
        return list(self._states)

    def set_state(self, state: str) -> bool:
        if state not in self._states:
            raise ValueError(
                f"Invalid state '{state}'. Available: {self._states}"
            )
        self._state = state
        return True

    def get_current_state(self) -> str:
        return self._state

    def get_power(self) -> float | None:
        if self._state == 'off':
            return 0.0
        idx = self._states.index(self._state)
        return 1500.0 * idx / (len(self._states) - 1)

    def supports_countdown(self) -> bool:
        return True

    def set_countdown(self, seconds: int, target_state: str = 'off') -> bool:
        self._countdown = (seconds, target_state)
        return True

    def validate_config(self, driver_config: dict) -> None:
        if 'address' not in driver_config:
            raise ValueError("Missing required field: 'address'")

    @property
    def driver_name(self) -> str:
        return "mock"
    