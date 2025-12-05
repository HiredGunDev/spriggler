"""Utility helpers for working with Govee BLE sensor payloads."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from bleak import BleakScanner

GOVEE_H5100_MANUFACTURER_ID = 0x0001


class GoveeH5100Base(ABC):
    """Base class for Govee H5100 BLE sensors.

    Subclasses implement _extract_reading() to select which values from the
    decoded advertisement to expose. This keeps each sensor class focused on
    a single value stream while sharing the BLE advertisement machinery.
    """

    BLE_MANUFACTURER_ID = GOVEE_H5100_MANUFACTURER_ID

    def __init__(self, config: dict):
        self.config = dict(config)
        self.id = self.config.get("id")
        self.identifier = self.config.get("identifier") or self.id
        self.normalized_identifier = self._normalize_identifier(self.identifier)
        self.refresh_rate = self.config.get("refresh_rate", 30)

        # Raw decoded values from advertisements
        self._current_data: Dict[str, Any] = {}

        # Last emitted values for deduplication
        self._last_emitted: Dict[str, Any] = {}
        self.suppressed_identical_advertisements = 0
        self.has_logged_no_data = False

        self.logger = None
        self.scanner = None

    @abstractmethod
    def _extract_reading(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the relevant reading from decoded advertisement data.

        Args:
            decoded: Full decoded payload with temperature, humidity, battery.

        Returns:
            Dict with only the fields this sensor exposes (e.g. {"humidity": ...}).
        """

    @abstractmethod
    def _format_log_message(self, reading: Dict[str, Any], suppressed: int) -> str:
        """Format a log message for a new reading."""

    @abstractmethod
    def _get_sensor_type(self) -> str:
        """Return the sensor type for metadata (e.g. 'temperature_sensor')."""

    @abstractmethod
    def _get_protocol_name(self) -> str:
        """Return the protocol name for metadata."""

    async def initialize(self, spriggler_logger) -> None:
        """Initialize the sensor with the Spriggler logging system."""
        self.logger = spriggler_logger.bind(
            COMPONENT_TYPE="sensor", ENTITY_NAME=self.identifier
        )
        self.logger.info(
            f"{self.__class__.__name__} sensor initialized.",
            config=self.config,
            normalized_identifier=self.normalized_identifier,
        )
        await self.start_scanning()

    def handle_advertisement(self, device, advertisement_data) -> None:
        """Process BLE advertisement data to extract sensor information."""
        if not self._matches_device(device, advertisement_data):
            return

        manufacturer_data = self._extract_manufacturer_data(advertisement_data)
        if not manufacturer_data:
            return

        decoded = decode_h5100_manufacturer_data(manufacturer_data)
        if not decoded:
            self.logger.warning("Failed to decode manufacturer data.")
            return

        self.logger.debug("Decoded manufacturer data", decoded_payload=decoded)

        # Store raw values (temperature converted to Fahrenheit)
        self._current_data = {
            "temperature": decoded["temperature"] * 1.8 + 32,
            "humidity": decoded["humidity"],
            "battery": decoded["battery"],
        }

        # Reset no-data guard once we have valid data
        self.has_logged_no_data = False

        # Extract only what this sensor cares about
        reading = self._extract_reading(self._current_data)

        # Deduplicate identical readings
        if self._last_emitted and reading == self._last_emitted:
            self.suppressed_identical_advertisements += 1
            return

        suppressed = self.suppressed_identical_advertisements
        self.suppressed_identical_advertisements = 0
        self._last_emitted = reading.copy()

        self.logger.info(self._format_log_message(reading, suppressed))

    # -------------------------------------------------------------------------
    # Compatibility properties for tests and legacy access
    # -------------------------------------------------------------------------
    @property
    def current_temperature(self) -> Optional[float]:
        """Temperature in Fahrenheit from latest advertisement."""
        return self._current_data.get("temperature")

    @property
    def current_humidity(self) -> Optional[float]:
        """Humidity percentage from latest advertisement."""
        return self._current_data.get("humidity")

    @property
    def battery_level(self) -> Optional[int]:
        """Battery percentage from latest advertisement."""
        return self._current_data.get("battery")

    def _matches_device(self, device, advertisement_data) -> bool:
        """Check if this advertisement is from our configured device."""
        device_address = getattr(device, "address", None)
        device_name = getattr(device, "name", None)
        advertisement_name = getattr(advertisement_data, "local_name", None)

        # Check for GVH5100_XXXX signature match
        expected_signature = f"GVH5100_{self.identifier}" if self.identifier else None
        if expected_signature:
            if any(
                expected_signature.lower() in (value or "").lower()
                for value in (advertisement_name, device_name)
            ):
                return True

        # If no device info provided (e.g. in tests), accept the advertisement
        if device is None:
            return True

        # Fall back to normalized identifier matching
        if self.normalized_identifier:
            normalized_address = self._normalize_identifier(device_address)
            normalized_name = self._normalize_identifier(device_name)

            if (
                normalized_address == self.normalized_identifier
                or normalized_name == self.normalized_identifier
            ):
                return True

            self.logger.debug(
                "Skipping advertisement due to identifier mismatch",
                configured_identifier=self.identifier,
                device_address=device_address,
                device_name=device_name,
            )
            return False

        # No identifier configured, accept any advertisement
        return True

    def _extract_manufacturer_data(self, advertisement_data) -> Optional[bytes]:
        """Extract manufacturer data from advertisement."""
        if not advertisement_data.manufacturer_data:
            return None

        data = advertisement_data.manufacturer_data.get(self.BLE_MANUFACTURER_ID)
        if data is None:
            # Try first available manufacturer data
            values = list(advertisement_data.manufacturer_data.values())
            if values:
                data = values[0]

        if not data:
            self.logger.debug(
                "No manufacturer payload available; skipping",
                available_ids=list(advertisement_data.manufacturer_data.keys()),
            )
            return None

        return data

    async def start_scanning(self) -> None:
        """Start scanning for BLE advertisements."""
        if self.scanner is None:
            self.scanner = get_shared_bleak_scanner()
            self.logger.debug(
                "Registering advertisement callback and acquiring shared BLE scanner",
                scanner_id=id(self.scanner),
            )
            register_shared_detection_callback(
                self.handle_advertisement, logger=self.logger
            )
        await ensure_shared_bleak_scanner_running(self.logger)

    async def stop_scanning(self) -> None:
        """Stop BLE scanning."""
        if self.scanner:
            self.logger.debug(
                "Stopping BLE scanning", scanner_id=id(self.scanner)
            )
            await stop_shared_bleak_scanner(self.logger)

    async def read(self) -> Dict[str, Any]:
        """Retrieve the most recent reading."""
        reading = self._extract_reading(self._current_data)

        # Check if we have valid data
        if not reading or all(v is None for v in reading.values()):
            if not self.has_logged_no_data:
                self.logger.warning("No sensor data available yet.")
                self.has_logged_no_data = True
            return {"error": "No sensor data available"}

        self.logger.debug("Returning latest sensor readings", **reading)
        return reading

    def get_metadata(self) -> Dict[str, Any]:
        """Return metadata about the sensor."""
        return {
            "id": self.identifier,
            "type": self._get_sensor_type(),
            "protocol": self._get_protocol_name(),
            "refresh_rate": self.refresh_rate,
        }

    @staticmethod
    def _normalize_identifier(value: Optional[str]) -> Optional[str]:
        """Normalize identifier for comparison."""
        if value is None:
            return None
        return value.lower().replace(":", "").replace("-", "").replace("_", "")

_shared_bleak_scanner: Optional[BleakScanner] = None
_shared_bleak_scanner_started: bool = False
_detection_callbacks: List[Callable] = []


def get_shared_bleak_scanner() -> BleakScanner:
    """Return a shared ``BleakScanner`` instance for Govee sensors.

    Running multiple scanners simultaneously can prevent callbacks from firing
    on some platforms. Using a single shared scanner ensures all Govee sensors
    see the same advertisements without competing BLE scan sessions.
    """

    global _shared_bleak_scanner

    if _shared_bleak_scanner is None:
        try:
            _shared_bleak_scanner = BleakScanner(
                detection_callback=_dispatch_detection,
                scanning_filter={"ManufacturerData": [GOVEE_H5100_MANUFACTURER_ID]},
            )
        except Exception:
            # Some platforms or Bleak versions may not support manufacturer data
            # filters. Fall back to a plain scanner so detection still works.
            _shared_bleak_scanner = BleakScanner(detection_callback=_dispatch_detection)
    return _shared_bleak_scanner


def register_shared_detection_callback(
    callback: Callable, *, logger=None
) -> None:
    """Register a detection callback without overwriting existing callbacks."""

    if callback not in _detection_callbacks:
        _detection_callbacks.append(callback)
        if logger:
            logger.debug(
                "Registered new BLE detection callback",
                callback_name=getattr(callback, "__name__", repr(callback)),
                total_callbacks=len(_detection_callbacks),
            )
    else:
        if logger:
            logger.debug(
                "Skipped duplicate BLE detection callback",
                callback_name=getattr(callback, "__name__", repr(callback)),
                total_callbacks=len(_detection_callbacks),
            )

    # ``register_detection_callback`` is retained for compatibility with older
    # Bleak versions while still preferring the ``detection_callback`` constructor
    # argument. Re-registering the dispatcher is safe and idempotent.
    scanner = get_shared_bleak_scanner()
    try:
        scanner.register_detection_callback(_dispatch_detection)
        if logger:
            logger.debug("Re-registered shared BLE detection dispatcher with scanner")
    except AttributeError:
        # Older Bleak versions may not expose the register API; the constructor
        # callback already covers those cases.
        if logger:
            logger.debug(
                "BleakScanner.register_detection_callback unavailable; relying on constructor callback"
            )
        pass


async def ensure_shared_bleak_scanner_running(logger=None) -> BleakScanner:
    """Ensure the shared ``BleakScanner`` is running without relying on ``is_scanning``."""

    global _shared_bleak_scanner_started

    scanner = get_shared_bleak_scanner()
    is_scanning = getattr(scanner, "is_scanning", None)

    if is_scanning is True or _shared_bleak_scanner_started:
        if logger:
            logger.debug(
                "Shared BLE scanner already running",
                is_scanning=is_scanning,
                started_flag=_shared_bleak_scanner_started,
            )
        return scanner

    if logger:
        logger.debug("Starting shared BLE scanner", is_scanning=is_scanning)
    await scanner.start()
    _shared_bleak_scanner_started = True
    if logger:
        logger.info("BLE scanning started.")
    return scanner


async def stop_shared_bleak_scanner(logger=None) -> None:
    """Stop the shared ``BleakScanner`` and reset the started flag."""

    global _shared_bleak_scanner_started

    if _shared_bleak_scanner is None:
        if logger:
            logger.debug("Shared BLE scanner stop requested but scanner is None")
        return

    is_scanning = getattr(_shared_bleak_scanner, "is_scanning", None)
    if is_scanning is False and not _shared_bleak_scanner_started:
        if logger:
            logger.debug(
                "Shared BLE scanner already stopped",
                is_scanning=is_scanning,
                started_flag=_shared_bleak_scanner_started,
            )
        return

    if logger:
        logger.debug(
            "Stopping shared BLE scanner", is_scanning=is_scanning, started_flag=_shared_bleak_scanner_started
        )
    await _shared_bleak_scanner.stop()
    _shared_bleak_scanner_started = False
    if logger:
        logger.info("BLE scanning stopped.")


def _dispatch_detection(device, advertisement_data) -> None:
    """Dispatch BLE detections to all registered callbacks."""

    for callback in list(_detection_callbacks):
        try:
            callback(device, advertisement_data)
        except Exception:
            # Best-effort fan-out; individual callback failures should not
            # prevent other listeners from receiving advertisements.
            continue


def decode_h5100_manufacturer_data(manufacturer_data: bytes) -> Optional[Dict[str, Optional[float]]]:
    """Decode raw manufacturer data emitted by Govee H5100 advertisements.

    The payload encodes temperature and humidity as a six digit integer where
    the first three digits represent temperature in tenths of a degree Celsius
    and the last three digits represent relative humidity in tenths of a
    percent.  An optional seventh byte provides the battery percentage.
    """

    if len(manufacturer_data) < 6:
        return None

    payload = bytes(manufacturer_data)
    temp_bytes = payload[2:5]
    temp_raw = int.from_bytes(temp_bytes, byteorder="big", signed=False)
    temp_raw_str = f"{temp_raw:06d}"

    temperature = int(temp_raw_str[:3]) / 10.0
    humidity = int(temp_raw_str[3:]) / 10.0
    battery = payload[6] if len(payload) >= 7 else None

    return {
        "temperature": temperature,
        "humidity": humidity,
        "battery": battery,
    }
