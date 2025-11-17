"""Utility helpers for working with Govee BLE sensor payloads."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from bleak import BleakScanner

GOVEE_H5100_MANUFACTURER_ID = 0x88EC

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
