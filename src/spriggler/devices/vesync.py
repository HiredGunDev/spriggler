"""VeSync humidifier device driver.

Controls a VeSync-compatible humidifier (Levoit Dual 200S, Classic 300S,
etc.) via the VeSync cloud API.

Graduated device: states are ["off", "low", "high"] by default,
mapped to the humidifier's hardware mist levels.  The Dual 200S has
2 levels [1, 2].  The Classic 300S has 9 levels [1-9].

Cloud-only control — commands go through smartapi.vesync.com.  There
is no local API.  Commands are fire-and-forget: send the command,
optimistically update local state, verify through sensor feedback.
The VeSync cloud silently drops commands sometimes.  Patient retry
(at the controller level, not the driver level) handles this.

Config:
    [devices.seedling_humidifier]
    driver = "vesync_humidifier"
    type = "energy"
    states = ["off", "low", "high"]

    [devices.seedling_humidifier.driver_config]
    name = "Dual 200S"
    email = "$SPRIGGLER_VESYNC_EMAIL"
    password = "$SPRIGGLER_VESYNC_PASSWORD"

Dependencies:
    pip install pyvesync
    (installed via: pip install spriggler[vesync])
"""

from __future__ import annotations

import logging

from spriggler.devices import DeviceDriver, DeviceCommandError, driver_registry

log = logging.getLogger("spriggler.devices.vesync")


# Default state → mist level mapping for the Dual 200S
_DEFAULT_STATE_LEVELS = {"low": 1, "high": 2}


class VeSyncHumidifier(DeviceDriver):
    """Device driver for VeSync humidifiers.

    Graduated device: states ["off", "low", "high"] mapped to
    mist levels.  No local power monitoring (cloud-only device).
    """

    def __init__(self, device_name: str, driver_config: dict) -> None:
        self.validate_config(driver_config)
        self._device_name = device_name
        self._vesync_name = driver_config["name"]
        self._email = driver_config.get("email")
        self._password = driver_config.get("password")

        # State → mist level mapping
        state_map = driver_config.get("state_levels", _DEFAULT_STATE_LEVELS)
        self._state_levels = dict(state_map)

        # Build ordered state list: off first, then by ascending level
        sorted_states = sorted(
            self._state_levels.items(), key=lambda x: x[1]
        )
        self._states = ["off"] + [name for name, _ in sorted_states]

        # Reverse: level → state name (for query_hardware_state)
        self._level_to_state = {
            level: name for name, level in self._state_levels.items()
        }

        self._device = None  # pyvesync handle, resolved lazily
        self._last_commanded_state = "off"

    def _ensure_device(self):
        """Resolve the VeSync manager and device handle."""
        if self._device is not None:
            return
        from spriggler.devices.vesync_mgr import get_vesync_manager, VeSyncError
        try:
            mgr = get_vesync_manager(
                email=self._email,
                password=self._password,
            )
            self._device = mgr.get_humidifier(self._vesync_name)
        except VeSyncError as e:
            raise DeviceCommandError(
                f"Cannot find VeSync humidifier '{self._vesync_name}': {e}"
            ) from e

    def get_states(self) -> list[str]:
        return list(self._states)

    def set_state(self, state: str) -> bool:
        """Command the humidifier — fire and forget.

        Sends the command to the cloud and optimistically updates
        local state.  Does NOT block waiting for confirmation.
        The controller verifies through sensor feedback.
        """
        available = self.get_states()
        if state not in available:
            raise ValueError(
                f"Invalid state '{state}' for {self._device_name}. "
                f"Available: {available}"
            )

        if state == "off":
            return self._send_off()
        else:
            return self._send_mist(state)

    def _send_off(self) -> bool:
        from spriggler.devices.vesync_mgr import get_vesync_manager, VeSyncError
        try:
            self._ensure_device()
            mgr = get_vesync_manager()
            mgr.turn_off(self._device)
            self._last_commanded_state = "off"
            log.info("%s → off", self._device_name)
            return True
        except (VeSyncError, DeviceCommandError) as e:
            log.error("%s turn_off failed: %s", self._device_name, e)
            return False

    def _send_mist(self, state: str) -> bool:
        level = self._state_levels.get(state)
        if level is None:
            raise ValueError(f"Unknown state: {state}")
        from spriggler.devices.vesync_mgr import get_vesync_manager, VeSyncError
        try:
            self._ensure_device()
            mgr = get_vesync_manager()
            mgr.turn_on(self._device)
            mgr.set_mist_level(self._device, level)
            self._last_commanded_state = state
            log.info("%s → %s (level %d)", self._device_name, state, level)
            return True
        except (VeSyncError, DeviceCommandError) as e:
            log.error("%s set_mist(%s, level=%d) failed: %s",
                      self._device_name, state, level, e)
            return False

    def query_hardware_state(self) -> str:
        """Query actual state from cloud — for CLI diagnostics only.

        NOT used in the control loop (which uses sensor feedback).
        Cloud queries are slow and rate-limited.
        """
        from spriggler.devices.vesync_mgr import get_vesync_manager, VeSyncError
        try:
            self._ensure_device()
            mgr = get_vesync_manager()
            mgr.update_device(self._device)
            if not self._device.is_on:
                return "off"
            try:
                level = self._device.state.mist_virtual_level or 0
            except AttributeError:
                level = 0
            if level == 0:
                return "off"
            return self._closest_state(level)
        except (VeSyncError, DeviceCommandError, AttributeError):
            return self._last_commanded_state

    def _closest_state(self, level: int) -> str:
        """Map an arbitrary mist level to the nearest defined state."""
        if level == 0:
            return "off"
        best_name = None
        best_dist = 999
        for name, lvl in self._state_levels.items():
            dist = abs(lvl - level)
            if dist < best_dist:
                best_dist = dist
                best_name = name
        return best_name or "off"

    def validate_config(self, driver_config: dict) -> None:
        if "name" not in driver_config:
            raise ValueError(
                "VeSync driver requires 'name' in driver_config "
                "(the device name as set in the VeSync app)"
            )

        email = driver_config.get("email")
        password = driver_config.get("password")

        if not email or not password:
            raise ValueError(
                "VeSync driver requires 'email' and 'password' in "
                "driver_config.  Set them in config.toml using "
                "$SPRIGGLER_VESYNC_EMAIL / $SPRIGGLER_VESYNC_PASSWORD "
                "and populate ~/.spriggler/.env"
            )

        state_levels = driver_config.get("state_levels")
        if state_levels is not None:
            if not isinstance(state_levels, dict):
                raise ValueError(
                    "'state_levels' must be a dict mapping state names "
                    "to mist levels (e.g., {low = 1, high = 2})"
                )
            for name, level in state_levels.items():
                if name == "off":
                    raise ValueError(
                        "'off' is reserved and cannot be in 'state_levels'"
                    )
                if not isinstance(level, int) or level < 1:
                    raise ValueError(
                        f"Mist level for '{name}' must be a positive "
                        f"integer, got {level}"
                    )

    @property
    def driver_name(self) -> str:
        return "vesync_humidifier"

    @property
    def vesync_name(self) -> str:
        return self._vesync_name


# ── Self-register ────────────────────────────────────────────────

driver_registry.register("vesync_humidifier", VeSyncHumidifier)
