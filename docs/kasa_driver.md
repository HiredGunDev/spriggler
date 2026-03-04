# KASA Driver Documentation

## Overview

The KASA driver provides on/off control and power monitoring for TP-Link KASA smart plugs and power strips. It is Spriggler's primary hardware control layer for grow room equipment — lights, fans, heaters, humidifiers, and dehumidifiers are all controlled through KASA strip outlets.

The driver consists of four components:

- **KasaConnectionManager** (`kasa.py`) — singleton async bridge that handles device discovery by name, connection lifecycle, and python-kasa's async-to-sync bridging
- **KasaDevice** (`kasa_device.py`) — DeviceDriver subclass for on/off control of a specific outlet on a KASA strip
- **KasaPowerSensor** (`kasa_power.py`) — PowerSensor subclass providing real-time wattage monitoring, emergency power cutoff, and hardware countdown timers
- **PowerSensor ABC** (`power.py`) — abstract base class defining the power monitoring interface (vendor-neutral)

## Supported Hardware

### Recommended: TP-Link HS300 Power Strip

The HS300 is a 6-outlet WiFi smart power strip with individual outlet control and per-outlet energy monitoring. It is Spriggler's reference hardware because it is cheap ($25-35), widely available, individually addressable per outlet, has built-in energy monitoring (watts, volts, amps), supports hardware countdown timers (safety failsafe), and uses standard 15A US outlets.

Each outlet on the strip has a user-assigned name (alias) set through the KASA app. Spriggler identifies outlets by these names, not by IP address or outlet index.

### Also Supported

Any KASA smart plug with energy monitoring (KP115, EP25, HS110, etc.) works as a single-outlet device. Standalone plugs use the same driver with strip and plug names set to the same value.


## ⚠️ CRITICAL: Firmware Warnings

### Do Not Update KASA Firmware

**This is the single most important thing in this document.**

TP-Link has been pushing firmware updates to KASA devices that break local API control. Specifically, firmware updates on the HS300 (Hardware Version 2.0) switch the device from the original XOR-based local protocol to "KLAP v2" (Login Version 2), which as of February 2026 **cannot be authenticated by python-kasa or any third-party tool**.

The symptoms are:

- Discovery succeeds — the device responds with its model, MAC, and encryption scheme
- Handshake1 succeeds — the KLAP session initiates correctly
- Handshake2 fails — "Device response did not match our challenge" every time
- The device is fully functional through the KASA phone app but unreachable by python-kasa

This is not a credentials issue. The KLAP v2 handshake hash derivation has changed in a way that has not yet been reverse-engineered. It affects all HS300 HW v2.0 units with firmware 1.1.6 Build 240130 or later. The python-kasa community is actively working on this (see github.com/python-kasa/python-kasa issue #1604 and PR #1625).

**There is no firmware downgrade path.** TP-Link does not provide tools or support for rolling back KASA smart device firmware. Once updated, the device is permanently on the new firmware.

### How to Block Firmware Updates

The KASA app will prompt you to update firmware. **Always decline.** Additionally, take these steps to prevent accidental or automatic updates:

**Option A: Isolate the IoT VLAN (recommended)**

Place all KASA devices on a dedicated WiFi SSID/VLAN with no internet access. The devices communicate with Spriggler over the local network and do not need internet for any Spriggler function. This also prevents TP-Link from collecting telemetry.

**Option B: Block TP-Link update servers at the router**

If you cannot create a separate VLAN, block these domains at your router's DNS or firewall:

```
n-deventry.tplinkcloud.com
n-devs.tplinkcloud.com
euw1-api.tplinkcloud.com
use1-api.tplinkcloud.com
aps1-api.tplinkcloud.com
```

This blocks firmware update checks and cloud telemetry while allowing local control to continue working.

**Option C: DNS sinkhole (Pi-hole, etc.)**

Add the above domains to your Pi-hole or other DNS sinkhole blacklist.

### Identifying Firmware Status

To check whether a specific device has been updated to KLAP v2:

```bash
kasa discover
```

Look at the discovery output for each device:

```
Encrypt Type: KLAP
Login version: 2
```

If you see `Login version: 2` and `new_klap: 1`, the device has been updated and **will not work with python-kasa** until the community fix lands. Devices showing the old XOR protocol or KLAP v1 (Login version 1 or no login version) work normally.

### What If My Strip Was Already Updated?

Hold onto it. The python-kasa community is actively reverse-engineering the KLAP v2 authentication (see github.com/python-kasa/python-kasa PR #1625, branch `fix-iot-klap-v2-auth`). When the fix ships, you will be able to use the updated strip with Spriggler by providing your TP-Link cloud credentials in the Spriggler config. You can use it as a test device in the meantime.

For immediate needs, replace the updated strip with an old-firmware HS300 or use individual Shelly plugs (see the Shelly driver documentation when available).

### Why Did TP-Link Do This?

TP-Link is migrating KASA devices to their Tapo cloud backend. The KLAP v2 protocol forces all device communication through cloud-mediated authentication, enabling telemetry collection, ecosystem lock-in, and potential subscription features. Their official position is that Home Assistant and other third-party local control tools are "not supported." This affects the entire Home Assistant community (millions of users) and is well-documented across TP-Link community forums, Home Assistant GitHub issues, and python-kasa discussions.

Spriggler's design principle is **local-only, no cloud dependency**. KASA old-firmware remains the recommended platform because it is cheap and locally controllable. For new deployments where old-firmware KASA cannot be sourced, Shelly plugs are the recommended alternative.


## Configuration

### Strip Outlet as Device Controller

A device controlled by a KASA strip outlet:

```json
{
  "exhaust_fan": {
    "type": "binary",
    "role": "exhaust",
    "driver": "kasa_strip",
    "driver_config": {
      "strip": "Grow Strip",
      "plug": "Exhaust Fan"
    }
  }
}
```

- `strip` — the alias of the HS300 strip (set in the KASA app)
- `plug` — the alias of the specific outlet on that strip (set in the KASA app)

### Strip Outlet as Power Sensor

A KASA outlet used purely for power monitoring and safety cutoff on a device controlled by a different driver:

```json
{
  "heater": {
    "type": "binary",
    "role": "heater",
    "driver": "vesync_plug",
    "driver_config": { "name": "Space Heater" },
    "power_sensor": {
      "driver": "kasa_strip",
      "driver_config": {
        "strip": "Monitoring Strip",
        "plug": "Heater Outlet"
      }
    }
  }
}
```

The power_sensor provides:
- Real-time wattage readings for the solver
- Emergency power cutoff (independent of the device's control driver)
- Hardware countdown timer (failsafe that runs on the strip itself — survives Spriggler crashes and Pi reboots)

### Standalone Plug

For a standalone KASA plug (not a strip), set strip and plug to the same name:

```json
{
  "driver_config": {
    "strip": "Desk Lamp",
    "plug": "Desk Lamp"
  }
}
```

### Credentials for KLAP-Authenticated Devices

If and when KLAP v2 support lands in python-kasa, devices on the new firmware will require your TP-Link cloud credentials. These can be provided via environment variables:

```bash
export KASA_USERNAME="your-email@example.com"
export KASA_PASSWORD="your-kasa-password"
```

Old-firmware devices do not require credentials.


## Architecture

### Discovery by Name

KASA devices are identified by their user-assigned alias, not by IP address. This is deliberate — DHCP leases change, IPs get reassigned, but the name "Grow Strip" stays the same. The KasaConnectionManager broadcasts UDP discovery packets and matches responses against the configured name.

Discovery happens at startup and periodically in the background. If a device moves to a new IP (DHCP renewal, router reboot), the next discovery cycle finds it automatically.

### Async Bridge

python-kasa is fully async (asyncio). Spriggler's daemon is synchronous. The KasaConnectionManager runs a dedicated event loop in a background thread and provides synchronous wrappers for all operations. This is the same pattern used by the Govee BLE sensor driver.

### Connection Sharing

One KasaConnectionManager instance is shared across all KASA devices. If three outlets on "Grow Strip" control three different devices, they share a single connection to the strip. The manager handles locking and update coordination.

### Hardware Countdown Timer (Safety Failsafe)

The HS300's countdown timer is a hardware feature that runs on the strip's own microcontroller. When Spriggler sets a 5-minute countdown to turn off an outlet, that timer keeps running even if:

- Spriggler crashes
- The Raspberry Pi reboots
- The network goes down
- The WiFi drops

The timer is the last line of defense in Spriggler's graduated safety system. It is set on every control cycle and refreshed as long as the daemon is healthy. If the daemon stops refreshing it, the timer expires and the outlet turns off — preventing a stuck-on heater from overheating the grow room.

**Note:** The countdown timer API required a one-line patch to python-kasa's `Countdown` module to expose `set_countdown()` on individual strip children. This patch must be maintained when updating python-kasa.


## python-kasa Dependency

Spriggler uses a locally-editable install of python-kasa. This is necessary for two reasons:

1. The countdown timer patch (one line, exposes `set_countdown` on strip children)
2. Future KLAP v2 fix (when PR #1625 or equivalent is merged)

### Installation

```bash
git clone https://github.com/python-kasa/python-kasa.git
cd python-kasa
pip install -e .
```

### Maintaining the Fork

The recommended approach is to track upstream and maintain local patches:

```bash
# Add the community fix fork as a remote (for KLAP v2 testing)
git remote add adamjacob https://github.com/adamjacobmuller/python-kasa.git
git fetch adamjacob

# The KLAP v2 fix branch (not yet working as of Feb 2026)
# git merge adamjacob/fix-iot-klap-v2-auth

# Track upstream releases
git remote add upstream https://github.com/python-kasa/python-kasa.git
git fetch upstream
```

When upstream merges the KLAP v2 fix, update from upstream and re-apply the countdown timer patch.


## Troubleshooting

### "Device not found" on startup

The strip alias in your config doesn't match any discovered device. Check:
- Is the strip powered on and connected to WiFi?
- Does the alias in the config exactly match the alias in the KASA app (case-sensitive)?
- Is the strip on the same network/subnet as the Pi running Spriggler?

### "Authentication failed for device"

The device has been updated to KLAP v2 firmware. See the firmware warnings section above. The device will not work until the python-kasa community ships a fix.

### Power readings return None

The device supports on/off but not energy monitoring. Use a device with an energy meter (HS300, HS110, KP115). Not all KASA plugs have energy monitoring — the HS105 and EP10, for example, do not.

### Countdown timer not working

Verify the countdown timer patch is applied to your local python-kasa. The upstream library does not expose `set_countdown` on strip child devices. Check the debug log for countdown-related errors.

### Sluggish response or timeouts

KASA devices communicate over WiFi. Weak signal, network congestion, or too-frequent polling can cause timeouts. The KasaConnectionManager has built-in retry logic and periodic rediscovery. If a specific device is consistently slow, check its WiFi signal strength (visible in the KASA app under device info).