"""Govee BLE sensor driver — v0.5.

Receives BLE advertisements from Govee H5100/H5075/compatible
temperature-humidity sensors.  Converts all values to SI
fundamental units at the driver boundary:
    temperature:       °C → Kelvin
    humidity:          %RH → absolute humidity (g/m³) via physics plugin

BLE scanning runs in a shared background thread.  Multiple
GoveeSensor instances share one scanner.

Addressing (macOS vs Linux):
    macOS CoreBluetooth hides real MAC addresses behind randomized
    UUIDs.  Govee sensors embed the last 4 hex of their MAC in
    the BLE local name (e.g., "GVH5100_2650").

    The driver accepts:
      - Name suffix: "2650" — matched against BLE local name (macOS)
      - Full MAC: "A4:C1:38:XX:XX:XX" — matched against address (Linux)

Dependencies:
    pip install govee-ble bleak
    (installed via: pip install spriggler[ble])
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import threading
import time

from spriggler.sensors.base import SensorDriver, SensorReading, SensorReadError
from spriggler.sensors import driver_registry

log = logging.getLogger("spriggler.sensors.govee")

# MAC address pattern: AA:BB:CC:DD:EE:FF
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# Name suffix pattern: 4+ hex chars
_SUFFIX_RE = re.compile(r"^[0-9A-Fa-f]{4,}$")

# Models that are gateways/hubs — ignore their advertisements
_GATEWAY_MODELS = {"H5151"}


def _is_gateway(local_name: str) -> bool:
    return any(model in local_name for model in _GATEWAY_MODELS)


class GoveeSensor(SensorDriver):
    """BLE sensor driver for Govee H5100 and compatible models.

    driver_config:
        address: BLE name suffix ("2650") or full MAC ("A4:C1:38:...")
        model:   Optional model identifier ("H5100")

    Returns SensorReading with SI fundamental units:
        temperature:       Kelvin (converted from °C)
        absolute_humidity: g/m³ (converted from %RH using physics plugin)
        battery:           percent
        signal_strength:   dBm
    """

    # ── Shared scanner (class-level) ─────────────────────────────
    # One BLE scanner thread serves all GoveeSensor instances.
    _lock = threading.Lock()
    _thread: threading.Thread | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _running = False
    _mac_instances: dict[str, GoveeSensor] = {}
    _suffix_instances: dict[str, GoveeSensor] = {}

    def __init__(self, sensor_name: str, driver_config: dict) -> None:
        self._name = sensor_name
        addr = driver_config["address"].upper()

        if _MAC_RE.match(addr):
            self._address = addr
            self._match_mode = "mac"
        elif _SUFFIX_RE.match(addr):
            self._address = addr
            self._match_mode = "suffix"
        else:
            raise ValueError(
                f"Invalid address '{addr}'. Use a MAC (A4:C1:38:XX:XX:XX) "
                f"or BLE name suffix (2650)."
            )

        # Late imports — these are optional dependencies
        try:
            from govee_ble import GoveeBluetoothDeviceData
        except ImportError:
            raise SensorReadError(
                "govee-ble not installed. Run: pip install spriggler[ble]"
            )

        self._parser = GoveeBluetoothDeviceData()

        # Cached reading
        self._reading: SensorReading | None = None
        self._reading_lock = threading.Lock()

        # Register and start scanner
        GoveeSensor._register_instance(self)

    def read(self) -> SensorReading | None:
        """Return the most recent reading, or None if never received.

        The driver always returns its latest reading with the original
        sample_time.  Freshness classification happens above this layer.
        """
        with self._reading_lock:
            return self._reading

    def validate_config(self, driver_config: dict) -> None:
        if "address" not in driver_config:
            raise ValueError("Missing required field: 'address'")
        addr = driver_config["address"]
        if not _MAC_RE.match(addr) and not _SUFFIX_RE.match(addr):
            raise ValueError(
                f"Invalid address: '{addr}'. "
                "Use a MAC (A4:C1:38:XX:XX:XX) or name suffix (2650)."
            )

    @property
    def driver_name(self) -> str:
        return "govee_ble"

    @property
    def sensor_name(self) -> str:
        return self._name

    @property
    def address(self) -> str:
        return self._address

    # ── Advertisement processing ─────────────────────────────────

    def _on_advertisement(self, device, advertisement_data) -> None:
        """Process a BLE advertisement for this sensor.

        Converts native sensor units to SI fundamentals at this
        boundary — nothing above ever sees °C or %RH.
        """
        try:
            from govee_ble import GoveeBluetoothDeviceData, SensorDeviceClass
            from habluetooth.models import BluetoothServiceInfo

            local_name = str(
                advertisement_data.local_name or device.name or ""
            )
            address = str(device.address)

            service_info = BluetoothServiceInfo(
                name=local_name,
                address=address,
                rssi=int(advertisement_data.rssi),
                manufacturer_data={
                    int(k): bytes(v)
                    for k, v in advertisement_data.manufacturer_data.items()
                },
                service_data={
                    str(k): bytes(v)
                    for k, v in advertisement_data.service_data.items()
                },
                service_uuids=[
                    str(u) for u in advertisement_data.service_uuids
                ],
                source="bleak",
            )

            if not self._parser.supported(service_info):
                return

            update = self._parser.update(service_info)

            # Extract native values
            temp_c: float | None = None
            rh: float | None = None
            battery: float | None = None
            rssi = float(advertisement_data.rssi)

            for key, value in update.entity_values.items():
                native = value.native_value
                if native is None:
                    continue
                desc = update.entity_descriptions.get(key)
                if desc is None:
                    continue

                if desc.device_class == SensorDeviceClass.TEMPERATURE:
                    temp_c = float(native)
                elif desc.device_class == SensorDeviceClass.HUMIDITY:
                    rh = float(native)
                elif desc.device_class == SensorDeviceClass.BATTERY:
                    battery = float(native)
                elif desc.device_class == SensorDeviceClass.SIGNAL_STRENGTH:
                    rssi = float(native)

            if temp_c is None:
                return  # No temperature = no useful reading

            # ── Convert to SI fundamentals at the driver boundary ──
            from spriggler.physics.temperature import celsius_to_kelvin
            temp_k = celsius_to_kelvin(temp_c)

            values: dict[str, float] = {"temperature": temp_k}

            # Convert %RH → absolute humidity (g/m³) using physics plugin
            if rh is not None:
                from spriggler.physics import registry
                if registry.has_plugin("humidity"):
                    ah = registry.to_fundamental(
                        "humidity", rh, temperature=temp_k
                    )
                    values["absolute_humidity"] = ah
                else:
                    # No plugin available — log warning, skip humidity
                    log.warning(
                        "%s: No humidity physics plugin — cannot convert "
                        "%%RH to absolute humidity.  Raw %%RH dropped.",
                        self._name,
                    )

            if battery is not None:
                values["battery"] = battery
            values["signal_strength"] = rssi

            now = time.time()
            reading = SensorReading(sample_time=now, values=values)

            with self._reading_lock:
                self._reading = reading

            log.debug(
                "%s: T=%.2fK AH=%.2fg/m³ bat=%s rssi=%.0f",
                self._name,
                temp_k,
                values.get("absolute_humidity", 0),
                battery or "?",
                rssi,
            )

        except Exception:
            log.exception("Error processing advertisement for %s", self._name)

    # ── Shared scanner management ────────────────────────────────

    @classmethod
    def _register_instance(cls, instance: GoveeSensor) -> None:
        with cls._lock:
            if instance._match_mode == "mac":
                cls._mac_instances[instance._address] = instance
            else:
                cls._suffix_instances[instance._address] = instance
            if not cls._running:
                cls._start_scanner()

    @classmethod
    def _find_instance(cls, address: str, local_name: str) -> GoveeSensor | None:
        # MAC match (Linux)
        inst = cls._mac_instances.get(address.upper())
        if inst is not None:
            return inst
        # Suffix match (macOS)
        if local_name:
            upper_name = local_name.upper()
            for suffix, inst in cls._suffix_instances.items():
                if upper_name.endswith(suffix):
                    return inst
        return None

    @classmethod
    def _start_scanner(cls) -> None:
        cls._running = True
        cls._thread = threading.Thread(
            target=cls._scanner_thread_fn,
            name="govee-ble-scanner",
            daemon=True,
        )
        cls._thread.start()
        total = len(cls._mac_instances) + len(cls._suffix_instances)
        log.info("BLE scanner started (%d sensor(s))", total)

    @classmethod
    def _scanner_thread_fn(cls) -> None:
        cls._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls._loop)
        try:
            cls._loop.run_until_complete(cls._scan_loop())
        except RuntimeError as e:
            # "Event loop stopped before Future completed" is normal
            # during shutdown — stop_scanner() stops the loop while
            # scan_loop is sleeping.
            if "stopped before" in str(e):
                pass
            else:
                log.exception("BLE scanner thread crashed")
        except Exception:
            log.exception("BLE scanner thread crashed")
        finally:
            cls._running = False
            try:
                cls._loop.close()
            except Exception:
                pass

    @classmethod
    async def _scan_loop(cls) -> None:
        from bleak import BleakScanner

        def on_detection(device, advertisement_data):
            local_name = str(
                advertisement_data.local_name or device.name or ""
            )
            if _is_gateway(local_name):
                return

            instance = cls._find_instance(str(device.address), local_name)
            if instance is not None:
                instance._on_advertisement(device, advertisement_data)

        scan_mode = "active" if platform.system() == "Darwin" else "passive"

        scanner = BleakScanner(
            detection_callback=on_detection,
            scanning_mode=scan_mode,
        )

        log.info("BLE %s scan starting...", scan_mode)
        while cls._running:
            try:
                async with scanner:
                    await asyncio.sleep(30)
            except Exception:
                log.exception("BLE scan error, retrying in 5s...")
                await asyncio.sleep(5)

    @classmethod
    def stop_scanner(cls) -> None:
        """Stop the shared BLE scanner.  Called on shutdown."""
        with cls._lock:
            cls._running = False
            if cls._loop and cls._loop.is_running():
                cls._loop.call_soon_threadsafe(cls._loop.stop)
            if cls._thread:
                cls._thread.join(timeout=5)
            cls._mac_instances.clear()
            cls._suffix_instances.clear()
            log.info("BLE scanner stopped")


# ── Self-register with the sensor driver registry ────────────────

driver_registry.register("govee_ble", GoveeSensor)
