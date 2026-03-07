"""VeSync humidifier device driver.

Controls a VeSync-compatible humidifier (Levoit Dual 200S, Classic 300S,
etc.) via the VeSync cloud API using pyvesync.

This is a graduated device.  The solver sees discrete mist levels,
mapped from the humidifier's hardware levels to a configurable set of
states.  The Dual 200S has 2 mist levels [1, 2].  The Classic 300S
has 9 levels [1-9].  Default states: ['off', 'low', 'high'] mapped
to the device's min and max levels.

Configuration:
    "driver_config": {
        "name": "Dual 200S",        # Device name in VeSync app
        "email": "user@example.com", # VeSync account email
        "password": "secret",        # VeSync account password
        "states": {                   # Optional: custom state mapping
            "low": 1,
            "high": 2
        }
    }

The email/password can also be provided via environment variables:
    VESYNC_EMAIL, VESYNC_PASSWORD

States mapping:
    The 'states' dict maps state names to mist levels.
    'off' is always present and maps to turning the device off.
    States are ordered by mist level for the solver.
    If no 'states' key is provided, defaults to {'low': 1, 'high': 2}
    which matches the Dual 200S.  For a Classic 300S with 9 levels,
    you might use {'low': 1, 'mid': 5, 'high': 9}.

Note on cloud control:
    VeSync devices are cloud-only — all commands go through
    smartapi.vesync.com.  There is no local control pathway.
    If internet is lost, the device continues in its last state
    until the tank empties or connectivity resumes.  The Govee
    sensors (local BLE) continue reading humidity regardless.
"""

import logging
import os

from spriggler.devices.base import DeviceDriver, DeviceCommandError
from spriggler.devices.vesync import get_vesync_manager, VeSyncError

log = logging.getLogger(__name__)


class VeSyncHumidifier(DeviceDriver):
    """Device driver for VeSync humidifiers.

    Graduated device: states are ['off', 'low', 'high'] by default,
    or custom states mapped to mist levels 1-9.
    No local power monitoring (cloud-only device).
    """

    # Default state-to-level mapping (Dual 200S: 2 levels)
    DEFAULT_STATES = {'low': 1, 'high': 2}

    def __init__(self, driver_config: dict) -> None:
        self.validate_config(driver_config)
        self._device_name = driver_config['name']

        # Credentials: config > environment variables
        self._email = driver_config.get(
            'email', os.environ.get('VESYNC_EMAIL'))
        self._password = driver_config.get(
            'password', os.environ.get('VESYNC_PASSWORD'))

        # State mapping: {state_name: mist_level}
        state_map = driver_config.get('states', self.DEFAULT_STATES)
        self._state_levels = dict(state_map)

        # Build ordered state list: off first, then by ascending level
        sorted_states = sorted(
            self._state_levels.items(), key=lambda x: x[1]
        )
        self._states = ['off'] + [name for name, _ in sorted_states]

        # Reverse map: mist_level -> state_name (for get_current_state)
        self._level_to_state = {
            level: name for name, level in self._state_levels.items()
        }

        self._mgr = None        # Resolved lazily
        self._device = None      # pyvesync device handle
        self._last_known_state = 'off'

    def _ensure_device(self):
        """Resolve the VeSync manager and device handle."""
        if self._device is not None:
            return
        try:
            if self._mgr is None:
                self._mgr = get_vesync_manager(
                    email=self._email,
                    password=self._password,
                )
            self._device = self._mgr.get_humidifier(self._device_name)
        except VeSyncError as e:
            raise DeviceCommandError(
                f"Cannot find VeSync humidifier '{self._device_name}': {e}"
            ) from e

    # ── State management ─────────────────────────────────────────────

    def get_available_states(self) -> list[str]:
        return list(self._states)

    def set_state(self, state: str) -> bool:
        available = self.get_available_states()
        if state not in available:
            raise ValueError(
                f"Invalid state '{state}' for {self.driver_name}. "
                f"Available: {available}"
            )
        if state == 'off':
            return self.turn_off()
        else:
            return self._set_mist(state)

    def get_current_state(self) -> str:
        try:
            self._ensure_device()
            self._mgr.update_device(self._device)

            if not self._device.is_on:
                self._last_known_state = 'off'
                return 'off'

            level = self._mgr.get_mist_level(self._device)
            if level == 0:
                self._last_known_state = 'off'
                return 'off'

            # Find the closest matching state
            state = self._closest_state(level)
            self._last_known_state = state
            return state

        except (VeSyncError, DeviceCommandError):
            return self._last_known_state

    def _closest_state(self, level: int) -> str:
        """Map an arbitrary mist level to the nearest defined state."""
        if level == 0:
            return 'off'
        best_name = None
        best_dist = 999
        for name, lvl in self._state_levels.items():
            dist = abs(lvl - level)
            if dist < best_dist:
                best_dist = dist
                best_name = name
        return best_name or 'off'

    # ── Base interface ───────────────────────────────────────────────

    def turn_on(self) -> bool:
        """Turn on at highest mist level."""
        return self._set_mist(self._states[-1])

    def turn_off(self) -> bool:
        try:
            self._ensure_device()
            result = self._mgr.turn_off_device(self._device)
            if result:
                self._last_known_state = 'off'
            return result
        except (VeSyncError, DeviceCommandError) as e:
            log.error("VeSync turn_off failed for %s: %s",
                      self._device_name, e)
            return False

    def _set_mist(self, state: str) -> bool:
        """Turn on and set mist to the level for the given state."""
        level = self._state_levels.get(state)
        if level is None:
            raise ValueError(f"Unknown state: {state}")
        try:
            self._ensure_device()
            # Ensure device is on first
            self._mgr.turn_on_device(self._device)
            result = self._mgr.set_mist_level(self._device, level)
            if result:
                self._last_known_state = state
            return result
        except (VeSyncError, DeviceCommandError) as e:
            log.error("VeSync set_mist failed for %s (state=%s, level=%d): %s",
                      self._device_name, state, level, e)
            return False

    def is_on(self) -> bool:
        try:
            self._ensure_device()
            return self._mgr.is_device_on(self._device)
        except (VeSyncError, DeviceCommandError):
            return self._last_known_state != 'off'

    def get_power(self) -> float | None:
        # VeSync humidifiers don't report power consumption
        return None

    # ── Config validation ────────────────────────────────────────────

    def validate_config(self, driver_config: dict) -> None:
        if 'name' not in driver_config:
            raise ValueError(
                "VeSync humidifier requires 'name' in driver_config "
                "(the device name as set in the VeSync app)"
            )

        email = driver_config.get(
            'email', os.environ.get('VESYNC_EMAIL'))
        password = driver_config.get(
            'password', os.environ.get('VESYNC_PASSWORD'))

        if not email or not password:
            raise ValueError(
                "VeSync humidifier requires credentials. "
                "Set 'email' and 'password' in driver_config, "
                "or set VESYNC_EMAIL and VESYNC_PASSWORD env vars."
            )

        states = driver_config.get('states')
        if states is not None:
            if not isinstance(states, dict):
                raise ValueError(
                    "'states' must be a dict mapping state names to "
                    "mist levels (1-9)"
                )
            for name, level in states.items():
                if not isinstance(name, str):
                    raise ValueError(
                        f"State name must be a string, got {type(name)}"
                    )
                if name == 'off':
                    raise ValueError(
                        "'off' is reserved and cannot be in 'states'"
                    )
                if not isinstance(level, int) or level < 1:
                    raise ValueError(
                        f"Mist level for '{name}' must be a positive "
                        f"integer, got {level}"
                    )

    @property
    def driver_name(self) -> str:
        return "vesync_humidifier"