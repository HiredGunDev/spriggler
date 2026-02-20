"""Govee BLE sensor driver.

Uses the govee-ble library to parse BLE advertisement data from
Govee temperature/humidity sensors. Tested with H5100 but should
work with any model supported by govee-ble.

BLE scanning runs in a background thread. The daemon's sync read()
call returns the most recent cached reading, or None if no
advertisement has been received within the staleness window.

Dependencies:
    pip install govee-ble bleak
"""

import asyncio
import logging
import re
import threading
import time

from bleak import BleakScanner
from govee_ble import GoveeBluetoothDeviceData, SensorDeviceClass
from habluetooth.models import BluetoothServiceInfo

from spriggler.sensors.base import SensorDriver, SensorReadError


log = logging.getLogger('spriggler.govee')


def _celsius_to_kelvin(c: float) -> float:
    return c + 273.15


class GoveeSensor(SensorDriver):
    """BLE sensor driver for Govee H5100 and compatible models.

    driver_config:
        address: BLE MAC address (e.g., "A4:C1:38:XX:XX:XX")
        scan_timeout: seconds to consider a reading stale (default: 120)

    The driver maintains a background BLE scanner that receives
    advertisements from all Govee devices. Each read() returns the
    most recent parsed data for this sensor's MAC address.

    Multiple GoveeSensor instances share a single scanner via the
    class-level scanner management.
    """

    # ── Class-level scanner management ───────────────────────────────
    # One BLE scanner serves all GoveeSensor instances.
    _scanner_lock = threading.Lock()
    _scanner_thread = None
    _scanner_loop = None
    _scanner_running = False
    _instances: dict[str, 'GoveeSensor'] = {}  # address -> instance

    def __init__(self, driver_config: dict) -> None:
        self._address = driver_config['address'].upper()
        self._scan_timeout = driver_config.get('scan_timeout', 120)

        # govee-ble parser for this device
        self._parser = GoveeBluetoothDeviceData()

        # Cached reading and timestamp
        self._last_reading: dict | None = None
        self._last_reading_time: float = 0.0
        self._reading_lock = threading.Lock()

        # Register this instance and start scanner if needed
        GoveeSensor._register(self)

    def read(self) -> dict | None:
        """Return the most recent reading, or None if stale.

        Readings are populated by the background scanner callback.
        This method just returns the cache — it never blocks on BLE.
        """
        with self._reading_lock:
            if self._last_reading is None:
                return None

            age = time.time() - self._last_reading_time
            if age > self._scan_timeout:
                log.debug("Reading for %s is %.0fs old (stale after %ds)",
                          self._address, age, self._scan_timeout)
                return None

            return dict(self._last_reading)

    def validate_config(self, driver_config: dict) -> None:
        if 'address' not in driver_config:
            raise ValueError("Missing required field: 'address'")
        addr = driver_config['address']
        if not re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', addr):
            raise ValueError(f"Invalid MAC address: '{addr}'")

    @property
    def driver_name(self) -> str:
        return "govee_ble"

    # ── Advertisement callback ───────────────────────────────────────

    def _on_advertisement(self, service_info: BluetoothServiceInfo) -> None:
        """Called by the scanner when an advertisement is received
        from this sensor's MAC address.

        Parses the advertisement via govee-ble and caches the result.
        """
        try:
            # Check if govee-ble recognizes this device
            if not self._parser.supported(service_info):
                return

            update = self._parser.update(service_info)

            # Extract values from the SensorUpdate
            reading = {}
            for key, value in update.entity_values.items():
                native = value.native_value
                if native is None:
                    continue

                desc = update.entity_descriptions.get(key)
                if desc is None:
                    continue

                device_class = desc.device_class

                if device_class == SensorDeviceClass.TEMPERATURE:
                    # govee-ble returns Celsius
                    reading['temperature'] = _celsius_to_kelvin(float(native))
                elif device_class == SensorDeviceClass.HUMIDITY:
                    reading['humidity'] = float(native)
                elif device_class == SensorDeviceClass.BATTERY:
                    reading['battery'] = float(native)
                elif device_class == SensorDeviceClass.SIGNAL_STRENGTH:
                    reading['signal_strength'] = float(native)

            # RSSI comes from the service_info directly
            if 'signal_strength' not in reading:
                reading['signal_strength'] = service_info.rssi

            if reading:
                with self._reading_lock:
                    self._last_reading = reading
                    self._last_reading_time = time.time()

                log.debug("Govee %s: T=%.2fK H=%.1f%% B=%s",
                          self._address,
                          reading.get('temperature', 0),
                          reading.get('humidity', 0),
                          reading.get('battery', '?'))

        except Exception:
            log.exception("Error parsing advertisement from %s", self._address)

    # ── Class-level scanner lifecycle ────────────────────────────────

    @classmethod
    def _register(cls, instance: 'GoveeSensor') -> None:
        """Register a sensor instance and start the shared scanner."""
        with cls._scanner_lock:
            cls._instances[instance._address] = instance
            if not cls._scanner_running:
                cls._start_scanner()

    @classmethod
    def _start_scanner(cls) -> None:
        """Start the background BLE scanner thread."""
        cls._scanner_running = True
        cls._scanner_thread = threading.Thread(
            target=cls._scanner_thread_fn,
            name='govee-ble-scanner',
            daemon=True,
        )
        cls._scanner_thread.start()
        log.info("BLE scanner started (%d sensor(s) registered)",
                 len(cls._instances))

    @classmethod
    def _scanner_thread_fn(cls) -> None:
        """Background thread that runs the async BLE scanner."""
        cls._scanner_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls._scanner_loop)
        try:
            cls._scanner_loop.run_until_complete(cls._scan_forever())
        except Exception:
            log.exception("BLE scanner thread crashed")
        finally:
            cls._scanner_running = False
            cls._scanner_loop.close()
            log.warning("BLE scanner thread exited")

    @classmethod
    async def _scan_forever(cls) -> None:
        """Async scanner loop. Runs until the daemon exits."""
        def detection_callback(device, advertisement_data):
            address = device.address.upper()
            instance = cls._instances.get(address)
            if instance is None:
                return  # Not one of our sensors

            # Build BluetoothServiceInfo for govee-ble
            service_info = BluetoothServiceInfo(
                name=advertisement_data.local_name or device.name or "",
                address=address,
                rssi=advertisement_data.rssi,
                manufacturer_data=dict(advertisement_data.manufacturer_data),
                service_data=dict(advertisement_data.service_data),
                service_uuids=list(advertisement_data.service_uuids),
                source="bleak",
            )
            instance._on_advertisement(service_info)

        scanner = BleakScanner(
            detection_callback=detection_callback,
            scanning_mode="passive",
        )

        log.info("Starting BLE passive scan...")
        while cls._scanner_running:
            try:
                async with scanner:
                    # Run scanner in 30-second windows, then restart.
                    # Some BLE stacks get flaky on very long scans.
                    await asyncio.sleep(30)
            except Exception:
                log.exception("BLE scan error, retrying in 5s...")
                await asyncio.sleep(5)

    @classmethod
    def stop_scanner(cls) -> None:
        """Stop the shared BLE scanner. Called on daemon shutdown."""
        with cls._scanner_lock:
            cls._scanner_running = False
            if cls._scanner_loop and cls._scanner_loop.is_running():
                cls._scanner_loop.call_soon_threadsafe(
                    cls._scanner_loop.stop
                )
            if cls._scanner_thread:
                cls._scanner_thread.join(timeout=5)
            cls._instances.clear()
            log.info("BLE scanner stopped")
