"""KASA power sensor for monitoring and hardware failsafe.

Monitors power consumption on a KASA smart plug or strip outlet.
Provides emergency power cutoff and hardware countdown timer as
safety features.

This is used as a power_sensor on device configs. The device itself
may be controlled by any driver (VeSync, KASA, anything). The KASA
strip just monitors the power and provides a kill switch.

Configuration (in a device's power_sensor block):
    "power_sensor": {
        "driver": "kasa_strip",
        "driver_config": {
            "strip": "Shed Strip",
            "plug": "Humidifier"
        }
    }
"""

import logging

from spriggler.devices.power import PowerSensor, PowerSensorError
from spriggler.devices.kasa import get_kasa_manager, KasaError

log = logging.getLogger(__name__)


class KasaPowerSensor(PowerSensor):
    """Power sensor backed by a KASA smart plug or strip outlet.

    Capabilities:
        - read_power(): real-time wattage from KASA energy monitoring
        - cut_power(): turn off the outlet (emergency kill)
        - restore_power(): turn the outlet back on
        - set_countdown(): hardware countdown timer for failsafe
    """

    def __init__(self, driver_config: dict) -> None:
        self.validate_config(driver_config)
        self._strip_name = driver_config['strip']
        self._plug_name = driver_config['plug']
        self._mgr = get_kasa_manager()
        self._plug = None  # Resolved lazily

    def _ensure_plug(self):
        """Resolve the plug reference if not yet done."""
        if self._plug is None:
            try:
                self._plug = self._mgr.get_plug(
                    self._strip_name, self._plug_name
                )
            except KasaError as e:
                raise PowerSensorError(
                    f"Cannot find KASA plug '{self._plug_name}' "
                    f"on '{self._strip_name}': {e}"
                ) from e

    def read_power(self) -> float | None:
        try:
            self._ensure_plug()
            return self._mgr.read_power(self._plug)
        except (KasaError, PowerSensorError) as e:
            log.warning("KASA read_power failed for %s/%s: %s",
                        self._strip_name, self._plug_name, e)
            return None

    def supports_cutoff(self) -> bool:
        return True

    def cut_power(self) -> bool:
        try:
            self._ensure_plug()
            self._mgr.turn_off(self._plug)
            log.warning("KASA power CUT for %s/%s",
                        self._strip_name, self._plug_name)
            return True
        except (KasaError, PowerSensorError) as e:
            log.error("KASA cut_power FAILED for %s/%s: %s",
                      self._strip_name, self._plug_name, e)
            return False

    def restore_power(self) -> bool:
        try:
            self._ensure_plug()
            self._mgr.turn_on(self._plug)
            log.info("KASA power RESTORED for %s/%s",
                     self._strip_name, self._plug_name)
            return True
        except (KasaError, PowerSensorError) as e:
            log.error("KASA restore_power FAILED for %s/%s: %s",
                      self._strip_name, self._plug_name, e)
            return False

    def supports_countdown(self) -> bool:
        try:
            self._ensure_plug()
            return self._mgr.has_countdown(self._plug)
        except (KasaError, PowerSensorError):
            return False

    def set_countdown(self, seconds: int, target_state: str = 'off') -> bool:
        try:
            self._ensure_plug()
            return self._mgr.set_countdown(
                self._plug, seconds, target_state
            )
        except (KasaError, PowerSensorError) as e:
            log.error("KASA set_countdown FAILED for %s/%s: %s",
                      self._strip_name, self._plug_name, e)
            return False

    def validate_config(self, driver_config: dict) -> None:
        if 'strip' not in driver_config:
            raise ValueError(
                "KASA power sensor requires 'strip' in driver_config "
                "(the KASA device alias set in the KASA app)"
            )
        if 'plug' not in driver_config:
            raise ValueError(
                "KASA power sensor requires 'plug' in driver_config "
                "(the plug alias on the strip)"
            )

    @property
    def driver_name(self) -> str:
        return "kasa_strip"
