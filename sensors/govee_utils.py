"""Utility helpers for working with Govee BLE sensor payloads."""
from __future__ import annotations

from typing import Dict, Optional

GOVEE_H5100_MANUFACTURER_ID = 0x88EC


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
