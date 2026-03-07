# VeSync Driver Documentation

## Overview

The VeSync driver provides graduated mist control for Levoit humidifiers via the VeSync cloud API. It is Spriggler's humidity control layer, complementing the KASA-controlled heaters, lights, and fans.

The driver consists of three components:

- **VeSyncConnectionManager** (`vesync.py`) — singleton async bridge that handles VeSync cloud login, device discovery by name, and pyvesync's async-to-sync bridging
- **VeSyncHumidifier** (`vesync_device.py`) — DeviceDriver subclass providing graduated mist level control
- **MockVeSyncManager** (`tests/mock_vesync.py`) — test double for unit testing without network access

## Supported Hardware

### Levoit Dual 200S (LUH-D301S)

The primary supported device. A compact ultrasonic humidifier with 2 mist levels, WiFi control via VeSync app, and a 1.5L tank (~10-15 hours at high mist). The device does not have power monitoring or a local control API.

Mist levels: 1 (low), 2 (high). The solver sees states `['off', 'low', 'high']`.

### Also Compatible

Any VeSync humidifier supported by pyvesync should work. The Classic 300S has 9 mist levels and could use a custom state mapping like `{'low': 1, 'mid': 5, 'high': 9}`. The LV600S adds warm mist. Only the Dual 200S has been tested with Spriggler.

## ⚠️ Cloud-Only Control

**VeSync devices have no local control pathway.** Every command goes through `smartapi.vesync.com`. There is no LAN discovery, no local API, no mDNS. Internet access is required for both the device and Spriggler.

This means:

- **Internet outage:** The humidifier continues in its last state until the tank empties or connectivity resumes. The daemon retries commands each cycle (every 15 seconds), so brief outages self-recover.
- **Power loss:** The Dual 200S does not resume on power restore. It stays off until a cloud command or physical button press turns it on. A KASA plug cannot proxy-control it because of this behavior.
- **Cloud outage:** Same as internet outage from Spriggler's perspective. VeSync cloud outages are rare but have occurred.

The Govee BLE sensors (fully local, no internet) continue monitoring humidity regardless of VeSync connectivity. The safety system sees humidity changes even when it cannot actuate the humidifier.

### Mitigations

The 1.5L tank bounds the worst case. At high mist, the tank empties in 10-15 hours. A stuck-on humidifier cannot fog indefinitely — it runs dry.

The daemon logs VeSync command failures. If connectivity is lost, the log shows a clear pattern of failed commands that indicates the issue.

For environments where cloud dependency is unacceptable, consider a dumb ultrasonic humidifier on a KASA plug (binary on/off, fully local, with power monitoring and countdown safety). The tradeoff is losing graduated mist levels and dealing with mineral buildup on cheap ultrasonic units.


## Configuration

### Basic Setup

Add the humidifier to your `config/config.json` devices section:

```json
"seedling_humidifier": {
    "driver": "vesync_humidifier",
    "environment": "seedling",
    "circuit": "seedling_circuit",
    "role": "humidifier",
    "driver_config": {
        "name": "Dual 200S",
        "email": "you@example.com",
        "password": "your_vesync_password"
    }
}
```

- `name` — the device name as set in the VeSync app (exact match required)
- `email` / `password` — your VeSync account credentials

### Credentials via Environment Variables

To keep credentials out of the config file:

```bash
export VESYNC_EMAIL="you@example.com"
export VESYNC_PASSWORD="your_vesync_password"
```

Then omit `email` and `password` from `driver_config`:

```json
"driver_config": {
    "name": "Dual 200S"
}
```

Config values take precedence over environment variables if both are present.

### Custom State Mapping

The default states `['off', 'low', 'high']` map to mist levels 1 and 2 (matching the Dual 200S hardware). For humidifiers with more levels, provide a custom mapping:

```json
"driver_config": {
    "name": "Classic 300S",
    "states": {
        "low": 1,
        "mid": 5,
        "high": 9
    }
}
```

States are ordered by level for the solver. The state name `'off'` is reserved and always present.


## Architecture

### Async Bridge

pyvesync v3 is fully async (aiohttp). Like the KASA manager, the VeSyncConnectionManager runs a dedicated asyncio event loop in a background thread and provides synchronous wrappers. The daemon calls `set_mist_level()`, `turn_on_device()`, etc. as blocking calls. The manager submits them to the background loop via `run_coroutine_threadsafe`.

### Connection Lifecycle

1. **Login** — On first use, the manager logs into VeSync cloud and discovers devices. The session persists across cycles.
2. **Device lookup** — `get_humidifier(name)` finds a device by its VeSync app name. Cached after first lookup.
3. **Commands** — `set_mist_level()`, `turn_on_device()`, `turn_off_device()` send commands through the cloud API. Each command sets mode to 'manual' first (disabling the device's auto-humidity mode).
4. **State tracking** — Cloud state readback is unreliable (2-10 second lag). The driver tracks state locally via `_last_known_state`. The daemon uses this local state, not cloud-reported state, for solver decisions.

### Singleton Pattern

One VeSyncConnectionManager is shared across all VeSync devices, matching the KASA pattern. The singleton is created on first driver initialization and lives for the daemon's lifetime. `get_vesync_manager()` handles creation and reuse.


## Calibration

The humidifier is calibrated like any other device:

```bash
spriggler --home ~/Projects/spriggler calibrate device --device seedling_humidifier
```

The calibration suite will run the humidifier at each state (low, high), measure the humidity rise rate, then observe the coast and decay. Because the humidifier has no power monitoring, power calibration is skipped for this device.

The calibrated humidity contribution per state feeds the solver, which then picks the optimal combination of heater + humidifier + fan states each cycle.


## Troubleshooting

### "VeSync login failed"

Check email/password. Try logging into the VeSync phone app to verify credentials. If the app works but Spriggler fails, check whether your VeSync account requires 2FA (not supported by pyvesync).

### "Humidifier not found"

The `name` in `driver_config` must exactly match the device name in the VeSync app, including capitalization and spaces. Run the exerciser script to list available devices:

```bash
python vesync_exercise.py --email you@email.com --password secret
```

### Commands succeed but device doesn't respond

VeSync cloud lag. Commands return True (the API accepted them) but the device takes 2-10 seconds to react. The daemon's 15-second cycle is long enough for this to resolve between cycles.

### "Humidifier mist level must be between 1 and 2"

You're sending a level outside the device's hardware range. The Dual 200S only supports levels 1 and 2. Check your `states` mapping in the config.

### Device stays on after daemon stops

The humidifier has no hardware countdown timer (unlike KASA plugs). When the daemon stops, the humidifier continues in its last state. You must manually turn it off via the VeSync app or the physical button. This is a known limitation of cloud-only devices.


## Dependencies

```bash
pip install pyvesync
```

Requires pyvesync >= 3.0 (async API with aiohttp). Python >= 3.11.