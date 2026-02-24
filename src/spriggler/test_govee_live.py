#!/usr/bin/env python3
"""Live Govee BLE sensor test.

Run this to verify your Govee sensors are being received.
No daemon, no config, just raw BLE scanning and parsing.

Usage:
    python test_govee_live.py

Press Ctrl-C to stop.
"""

import asyncio
import logging
import sys
import time

from bleak import BleakScanner
from govee_ble import GoveeBluetoothDeviceData, SensorDeviceClass
from habluetooth.models import BluetoothServiceInfo

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('govee_live')

# Your sensors — use the last 4 hex from the BLE name
# (e.g., GVH5100_2C6A → "2C6A")
SENSORS = {
    '2C6A': 'shed_flower',
    '627A': 'outdoor',
    '0353': 'shed_veg',
    '5247': 'dryer',
    '2650': 'seedling',
    '2786': 'plenum'
}

# Models to silently ignore (gateways, hubs, non-sensor devices)
IGNORE_MODELS = {'H5151'}

# One parser per sensor
parsers = {suffix: GoveeBluetoothDeviceData() for suffix in SENSORS}
last_seen = {}


def celsius_to_fahrenheit(c):
    return c * 9 / 5 + 32


def match_sensor(local_name):
    """Match a BLE local name to a known sensor by suffix."""
    if not local_name:
        return None
    for suffix in SENSORS:
        if local_name.endswith(suffix):
            return suffix
    return None


def should_ignore(local_name):
    """Return True for gateway/hub models we don't care about."""
    for model in IGNORE_MODELS:
        if model in local_name:
            return True
    return False


def on_detection(device, advertisement_data):
    name = str(advertisement_data.local_name or device.name or "")

    # Skip gateways entirely
    if should_ignore(name):
        return

    matched = match_sensor(name)

    if matched is None:
        if 'GV' in name or 'Govee' in name:
            log.info("  UNKNOWN Govee: %-20s  name=%-25s  rssi=%d",
                     str(device.address)[:20], name, advertisement_data.rssi)
        return

    label = SENSORS[matched]
    parser = parsers[matched]

    # macOS CoreBluetooth returns ObjC string types — cast everything
    # to plain Python str for habluetooth's Cython layer.
    service_info = BluetoothServiceInfo(
        name=str(name),
        address=str(device.address),
        rssi=int(advertisement_data.rssi),
        manufacturer_data={int(k): bytes(v) for k, v in advertisement_data.manufacturer_data.items()},
        service_data={str(k): bytes(v) for k, v in advertisement_data.service_data.items()},
        service_uuids=[str(u) for u in advertisement_data.service_uuids],
        source="bleak",
    )

    if not parser.supported(service_info):
        return

    update = parser.update(service_info)

    temp_c = None
    humidity = None
    battery = None

    for key, value in update.entity_values.items():
        desc = update.entity_descriptions.get(key)
        if desc is None or value.native_value is None:
            continue
        if desc.device_class == SensorDeviceClass.TEMPERATURE:
            temp_c = float(value.native_value)
        elif desc.device_class == SensorDeviceClass.HUMIDITY:
            humidity = float(value.native_value)
        elif desc.device_class == SensorDeviceClass.BATTERY:
            battery = float(value.native_value)

    now = time.time()
    gap = ""
    if matched in last_seen:
        gap = f"  (gap: {now - last_seen[matched]:.1f}s)"
    last_seen[matched] = now

    parts = [f"  {label:15s}"]
    if temp_c is not None:
        temp_f = celsius_to_fahrenheit(temp_c)
        parts.append(f"T:{temp_f:.1f}F ({temp_c:.1f}C)")
    if humidity is not None:
        parts.append(f"H:{humidity:.1f}%")
    if battery is not None:
        parts.append(f"B:{battery:.0f}%")
    parts.append(f"rssi:{advertisement_data.rssi}")
    parts.append(gap)

    log.info("  ".join(parts))


async def main():
    log.info("Scanning for Govee sensors...")
    log.info("Known sensors (by name suffix):")
    for suffix, label in SENSORS.items():
        log.info("  *%s  →  %s", suffix, label)
    log.info("Ignoring: %s", ', '.join(IGNORE_MODELS))
    log.info("─" * 60)
    log.info("Waiting for advertisements (Ctrl-C to stop)...")
    log.info("")

    scanner = BleakScanner(
        detection_callback=on_detection,
        scanning_mode="active",
    )

    async with scanner:
        while True:
            await asyncio.sleep(1)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("")
        log.info("Stopped.")
        if last_seen:
            log.info("Sensors seen:")
            for suffix, t in last_seen.items():
                log.info("  %s: last seen %.0fs ago",
                         SENSORS.get(suffix, suffix),
                         time.time() - t)
        else:
            log.info("No known sensors detected.")