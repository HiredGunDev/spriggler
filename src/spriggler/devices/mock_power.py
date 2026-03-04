"""Mock power sensor for testing.

Simulates power monitoring and failsafe capabilities.
Used by the daemon in tests and demo mode.
"""

from spriggler.devices.power import PowerSensor


class MockPowerSensor(PowerSensor):
    """Simulated power sensor with configurable behavior.

    driver_config options:
        strip: (required for validate_config) fake strip name
        plug: (required for validate_config) fake plug name
        watts: simulated power reading (default: 0.0)
        has_cutoff: whether cut_power() works (default: True)
        has_countdown: whether set_countdown() works (default: True)
    """

    def __init__(self, driver_config: dict) -> None:
        self._config = driver_config
        self._watts = driver_config.get('watts', 0.0)
        self._has_cutoff = driver_config.get('has_cutoff', True)
        self._has_countdown = driver_config.get('has_countdown', True)
        self._power_cut = False
        self._countdown = None

    def read_power(self) -> float | None:
        if self._power_cut:
            return 0.0
        return self._watts

    def supports_cutoff(self) -> bool:
        return self._has_cutoff

    def cut_power(self) -> bool:
        if not self._has_cutoff:
            raise NotImplementedError
        self._power_cut = True
        return True

    def restore_power(self) -> bool:
        if not self._has_cutoff:
            raise NotImplementedError
        self._power_cut = False
        return True

    def supports_countdown(self) -> bool:
        return self._has_countdown

    def set_countdown(self, seconds: int, target_state: str = 'off') -> bool:
        if not self._has_countdown:
            raise NotImplementedError
        self._countdown = (seconds, target_state)
        return True

    def validate_config(self, driver_config: dict) -> None:
        if 'strip' not in driver_config:
            raise ValueError("Missing required field: 'strip'")
        if 'plug' not in driver_config:
            raise ValueError("Missing required field: 'plug'")

    @property
    def driver_name(self) -> str:
        return "mock_power"

    # ── Test helpers ─────────────────────────────────────────────
    def set_watts(self, watts: float) -> None:
        """Set the simulated power reading."""
        self._watts = watts

    @property
    def is_cut(self) -> bool:
        """Check if power has been cut."""
        return self._power_cut

    @property
    def last_countdown(self) -> tuple[int, str] | None:
        """Return the last countdown that was set."""
        return self._countdown
