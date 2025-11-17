"""Utility helpers for working with Govee BLE sensor payloads."""
from __future__ import annotations

from typing import Dict, Optional

from bleak import BleakScanner

GOVEE_H5100_MANUFACTURER_ID = 0x88EC

_shared_bleak_scanner: Optional[BleakScanner] = None
_shared_bleak_scanner_started: bool = False


def get_shared_bleak_scanner() -> BleakScanner:
    """Return a shared ``BleakScanner`` instance for Govee sensors.

    Running multiple scanners simultaneously can prevent callbacks from firing
    on some platforms. Using a single shared scanner ensures all Govee sensors
    see the same advertisements without competing BLE scan sessions.
    """

    global _shared_bleak_scanner

    if _shared_bleak_scanner is None:
        _shared_bleak_scanner = BleakScanner()
    return _shared_bleak_scanner


async def ensure_shared_bleak_scanner_running(logger=None) -> BleakScanner:
    """Ensure the shared ``BleakScanner`` is running without relying on ``is_scanning``."""

    global _shared_bleak_scanner_started

    scanner = get_shared_bleak_scanner()
    is_scanning = getattr(scanner, "is_scanning", None)

    if is_scanning is True or _shared_bleak_scanner_started:
        return scanner

    await scanner.start()
    _shared_bleak_scanner_started = True
    if logger:
        logger.info("BLE scanning started.")
    return scanner


async def stop_shared_bleak_scanner(logger=None) -> None:
    """Stop the shared ``BleakScanner`` and reset the started flag."""

    global _shared_bleak_scanner_started

    if _shared_bleak_scanner is None:
        return

    is_scanning = getattr(_shared_bleak_scanner, "is_scanning", None)
    if is_scanning is False and not _shared_bleak_scanner_started:
        return

    await _shared_bleak_scanner.stop()
    _shared_bleak_scanner_started = False
    if logger:
        logger.info("BLE scanning stopped.")


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
