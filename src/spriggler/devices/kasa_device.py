"""KASA device driver for smart plugs and power strips.

Controls on/off state of a KASA plug or a specific outlet on a
KASA power strip. Identified by device name (alias) and plug name,
not IP address. Names are set in the KASA app.

Configuration:
    "driver_config": {
        "strip": "Shed Strip",     # KASA device alias
        "plug": "Heater"           # Plug alias (same as strip for standalone)
    }

For a standalone plug (not a strip):
    "driver_config": {
        "strip": "Porch Heater",
        "plug": "Porch Heater"
    }
"""

import logging

from spriggler.devices.base import DeviceDriver, DeviceCommandError
from spriggler.devices.kasa import get_kasa_manager, KasaError

log = logging.getLogger(__name__)


class KasaDevice(DeviceDriver):
    """Device driver for KASA smart plugs and strip outlets.

    Binary device: states are ['off', 'on']. Control is on/off only.
    Power monitoring via get_power() if the hardware supports it.
    Hardware countdown timers for safety failsafe.
    """

    def __init__(self, driver_config: dict) -> None:
        self.validate_config(driver_config)
        self._strip_name = driver_config['strip']
        self._plug_name = driver_config['plug']
        self._mgr = get_kasa_manager()
        self._plug = None  # Resolved lazily on first use
        self._last_known_on = False

    def _ensure_plug(self):
        """Resolve the plug reference if not yet done."""
        if self._plug is None:
            try:
                self._plug = self._mgr.get_plug(
                    self._strip_name, self._plug_name
                )
            except KasaError as e:
                raise DeviceCommandError(
                    f"Cannot find KASA plug '{self._plug_name}' "
                    f"on '{self._strip_name}': {e}"
                ) from e

    def turn_on(self) -> bool:
        try:
            self._ensure_plug()
            self._mgr.turn_on(self._plug)
            self._last_known_on = True
            return True
        except (KasaError, DeviceCommandError) as e:
            log.error("KASA turn_on failed for %s/%s: %s",
                      self._strip_name, self._plug_name, e)
            return False

    def turn_off(self) -> bool:
        try:
            self._ensure_plug()
            self._mgr.turn_off(self._plug)
            self._last_known_on = False
            return True
        except (KasaError, DeviceCommandError) as e:
            log.error("KASA turn_off failed for %s/%s: %s",
                      self._strip_name, self._plug_name, e)
            return False

    def is_on(self) -> bool:
        try:
            self._ensure_plug()
            self._mgr.update_device(self._plug)
            self._last_known_on = self._mgr.is_on(self._plug)
            return self._last_known_on
        except (KasaError, DeviceCommandError):
            return self._last_known_on

    def get_power(self) -> float | None:
        try:
            self._ensure_plug()
            return self._mgr.read_power(self._plug)
        except (KasaError, DeviceCommandError):
            return None

    def supports_countdown(self) -> bool:
        try:
            self._ensure_plug()
            return self._mgr.has_countdown(self._plug)
        except (KasaError, DeviceCommandError):
            return False

    def set_countdown(self, seconds: int, target_state: str = 'off') -> bool:
        try:
            self._ensure_plug()
            return self._mgr.set_countdown(
                self._plug, seconds, target_state
            )
        except (KasaError, DeviceCommandError) as e:
            log.error("KASA set_countdown failed for %s/%s: %s",
                      self._strip_name, self._plug_name, e)
            return False

    def validate_config(self, driver_config: dict) -> None:
        if 'strip' not in driver_config:
            raise ValueError(
                "KASA device driver requires 'strip' in driver_config "
                "(the KASA device alias set in the KASA app)"
            )
        if 'plug' not in driver_config:
            raise ValueError(
                "KASA device driver requires 'plug' in driver_config "
                "(the plug alias on the strip, or same as strip name "
                "for standalone plugs)"
            )

    @property
    def driver_name(self) -> str:
        return "kasa_plug"
