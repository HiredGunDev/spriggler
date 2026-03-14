"""KASA device driver for smart plugs and power strips.

Controls on/off state of individual plugs on a KASA power strip
or standalone KASA plugs.  Identified by device alias and plug alias
(set in the KASA app), not IP address.

Also provides power monitoring (read_power) and hardware countdown
timers (set_countdown) for safety failsafe.

Config:
    [devices.seedling_heater.driver_config]
    strip = "seedling"     # KASA device alias
    plug = "Heater"        # Plug alias on the strip

Dependencies:
    pip install python-kasa
    (installed via: pip install spriggler[kasa])
"""

from __future__ import annotations

import logging

from spriggler.devices import DeviceDriver, DeviceCommandError, driver_registry

log = logging.getLogger("spriggler.devices.kasa")


class KasaDevice(DeviceDriver):
    """Device driver for KASA smart plugs and strip outlets.

    Binary device: states ["off", "on"].
    Power monitoring via read_power() if hardware supports emeter.
    Hardware countdown timers for safety failsafe.
    """

    def __init__(self, device_name: str, driver_config: dict) -> None:
        self.validate_config(driver_config)
        self._device_name = device_name
        self._strip_name = driver_config["strip"]
        self._plug_name = driver_config["plug"]
        self._plug = None  # Resolved lazily
        self._last_commanded_state = "off"

    def _ensure_plug(self):
        """Resolve the plug reference via the KASA connection manager."""
        if self._plug is not None:
            return
        from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError
        try:
            mgr = get_kasa_manager()
            self._plug = mgr.get_plug(self._strip_name, self._plug_name)
        except KasaError as e:
            raise DeviceCommandError(
                f"Cannot find KASA plug '{self._plug_name}' "
                f"on '{self._strip_name}': {e}"
            ) from e

    def set_state(self, state: str) -> bool:
        """Command the plug on or off."""
        available = self.get_states()
        if state not in available:
            raise ValueError(
                f"Invalid state '{state}' for {self._device_name}. "
                f"Available: {available}"
            )

        from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError
        try:
            self._ensure_plug()
            mgr = get_kasa_manager()
            if state == "off":
                mgr.turn_off(self._plug)
            else:
                mgr.turn_on(self._plug)
            self._last_commanded_state = state
            log.info("%s → %s", self._device_name, state)
            return True
        except (KasaError, DeviceCommandError) as e:
            log.error("%s set_state(%s) failed: %s",
                      self._device_name, state, e)
            return False

    def read_power(self) -> float | None:
        """Read current power draw from cache (non-blocking).

        Returns the most recent background-updated reading.
        Falls back to a direct read only if no cache exists yet.
        """
        from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError
        try:
            self._ensure_plug()
            mgr = get_kasa_manager()

            # Try cached first (non-blocking)
            cached = mgr.read_power_cached(
                self._strip_name, self._plug_name)
            if cached is not None:
                return cached

            # No cache yet — do a blocking read (startup only)
            return mgr.read_power(self._plug)
        except (KasaError, DeviceCommandError):
            return None

    def query_hardware_state(self) -> str:
        """Query the plug's actual on/off state from hardware.

        This is for CLI display and diagnostics, NOT for the
        control loop (which uses sensor feedback instead).
        """
        from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError
        try:
            self._ensure_plug()
            mgr = get_kasa_manager()
            mgr.update_device(self._plug)
            return "on" if mgr.is_on(self._plug) else "off"
        except (KasaError, DeviceCommandError):
            return self._last_commanded_state

    def supports_countdown(self) -> bool:
        from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError
        try:
            self._ensure_plug()
            mgr = get_kasa_manager()
            return mgr.has_countdown(self._plug)
        except (KasaError, DeviceCommandError):
            return False

    def set_countdown(self, seconds: int, target_state: str = "off") -> bool:
        from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError
        try:
            self._ensure_plug()
            mgr = get_kasa_manager()
            return mgr.set_countdown(self._plug, seconds, target_state)
        except (KasaError, DeviceCommandError) as e:
            log.error("%s set_countdown failed: %s", self._device_name, e)
            return False

    def validate_config(self, driver_config: dict) -> None:
        if "strip" not in driver_config:
            raise ValueError(
                "KASA driver requires 'strip' in driver_config "
                "(the KASA device alias from the KASA app)"
            )
        if "plug" not in driver_config:
            raise ValueError(
                "KASA driver requires 'plug' in driver_config "
                "(the plug alias on the strip)"
            )

    @property
    def driver_name(self) -> str:
        return "kasa_plug"

    @property
    def strip_name(self) -> str:
        return self._strip_name

    @property
    def plug_name(self) -> str:
        return self._plug_name


# ── Self-register ────────────────────────────────────────────────

driver_registry.register("kasa_plug", KasaDevice)
