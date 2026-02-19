# Spriggler Configuration

## Overview

The configuration file describes what exists in your physical setup. It does NOT describe what devices do to the environment — that's learned during calibration.

Configuration is JSON. The file lives in `config/spriggler.json` by default, or specify a path with `--config`.

## Structure

```json
{
  "version": "0.3",
  "name": "My Grow",

  "environments": { ... },
  "sensors": { ... },
  "devices": { ... },
  "circuits": { ... },
  "schedules": { ... },
  "safety": { ... }
}
```

## Environments

An environment is a physical space with its own atmosphere. A grow tent, a shed chamber, a seedling pod — anything that has a measurable interior climate.

```json
"environments": {
  "veg": {
    "description": "Vegetative chamber",
    "connections": {
      "flower": {
        "via": "inter_fan",
        "bidirectional": false
      },
      "ambient": {
        "via": "exhaust_fan",
        "bidirectional": false
      }
    }
  },
  "flower": {
    "description": "Flowering chamber",
    "connections": {
      "ambient": {
        "via": "flower_exhaust",
        "bidirectional": false
      }
    }
  },
  "seedling_pod": {
    "description": "Seedling pod on back porch",
    "connections": {
      "ambient": {
        "via": "pod_vent",
        "bidirectional": false
      }
    }
  }
}
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `description` | No | Human-readable label |
| `connections` | No | Other environments this space exchanges air with |

**Connections** describe physical airflow paths. The `via` field references a device ID. `bidirectional` indicates whether the device moves air both ways (e.g., an open vent) or one way (e.g., a fan blowing from veg into flower).

The special environment `"ambient"` represents the outside world. It has no sensors or devices — its conditions are inferred from environments that connect to it, or from an ambient sensor if configured.

## Sensors

A sensor is a piece of hardware that reports measurements.

```json
"sensors": {
  "govee_veg": {
    "driver": "govee_h5100",
    "address": "A4:C1:38:XX:XX:XX",
    "environment": "veg",
    "properties": ["temperature", "humidity"],
    "poll_interval_seconds": 60
  },
  "govee_flower": {
    "driver": "govee_h5100",
    "address": "A4:C1:38:YY:YY:YY",
    "environment": "flower",
    "properties": ["temperature", "humidity"],
    "poll_interval_seconds": 60
  },
  "govee_pod": {
    "driver": "govee_h5100",
    "address": "A4:C1:38:ZZ:ZZ:ZZ",
    "environment": "seedling_pod",
    "properties": ["temperature", "humidity"],
    "poll_interval_seconds": 30
  },
  "govee_ambient": {
    "driver": "govee_h5100",
    "address": "A4:C1:38:AA:AA:AA",
    "environment": "ambient",
    "properties": ["temperature", "humidity"],
    "poll_interval_seconds": 120
  }
}
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `driver` | Yes | Driver name (must match a registered driver) |
| `address` | Yes | Hardware address (BLE MAC, IP, etc.) |
| `environment` | Yes | Which environment this sensor measures |
| `properties` | Yes | What this sensor reports |
| `poll_interval_seconds` | No | How often to read (default: 60) |

## Devices

A device is a piece of equipment that can be turned on or off to affect the environment.

```json
"devices": {
  "veg_heater": {
    "driver": "kasa_plug",
    "address": "192.168.1.101",
    "environment": "veg",
    "circuit": "shed_20a",
    "role": "heater",
    "power_monitoring": true
  },
  "veg_humidifier": {
    "driver": "vesync_humidifier",
    "address": "192.168.1.102",
    "environment": "veg",
    "circuit": "shed_20a",
    "role": "humidifier",
    "power_monitoring": false
  },
  "exhaust_fan": {
    "driver": "kasa_plug",
    "address": "192.168.1.103",
    "environment": "veg",
    "circuit": "shed_20a",
    "role": "exhaust",
    "power_monitoring": true
  },
  "inter_fan": {
    "driver": "kasa_plug",
    "address": "192.168.1.104",
    "environment": "veg",
    "circuit": "shed_20a",
    "role": "transfer",
    "power_monitoring": true
  },
  "veg_light": {
    "driver": "kasa_plug",
    "address": "192.168.1.105",
    "environment": "veg",
    "circuit": "shed_20a",
    "role": "light",
    "power_monitoring": true
  },
  "pod_heater": {
    "driver": "kasa_plug",
    "address": "192.168.1.110",
    "environment": "seedling_pod",
    "circuit": "porch_15a",
    "role": "heater",
    "power_monitoring": true
  }
}
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `driver` | Yes | Driver name |
| `address` | Yes | Hardware address (IP, etc.) |
| `environment` | Yes | Which environment this device affects |
| `circuit` | Yes | Which electrical circuit this device is on |
| `role` | Yes | What this device does (see roles below) |
| `power_monitoring` | No | Whether driver supports wattage reporting (default: false) |

**Roles:**

Roles tell the solver and safety monitor the *category* of device. They do NOT tell the system what the device does to the environment — that's learned during calibration.

| Role | Description |
|---|---|
| `heater` | Adds heat to the environment |
| `exhaust` | Moves air from environment to outside |
| `intake` | Moves air from outside into environment |
| `transfer` | Moves air between two environments |
| `humidifier` | Adds moisture to the environment |
| `dehumidifier` | Removes moisture from the environment |
| `light` | Grow light (also generates heat — learned during calibration) |
| `circulation` | Moves air within the environment |

Roles inform the safety monitor's default assumptions. A `heater` in safe state is OFF. An `exhaust` in safe state is ON. These defaults can be overridden in the safety config.

## Circuits

Circuits describe electrical capacity limits.

```json
"circuits": {
  "shed_20a": {
    "max_amps": 20,
    "voltage": 120,
    "description": "Shed main circuit"
  },
  "porch_15a": {
    "max_amps": 15,
    "voltage": 120,
    "description": "Back porch outlet"
  }
}
```

The solver uses circuit limits as hard constraints. It will never propose a device combination that exceeds circuit capacity.

**Fields:**

| Field | Required | Description |
|---|---|---|
| `max_amps` | Yes | Circuit breaker rating |
| `voltage` | Yes | Line voltage (for wattage calculations) |
| `description` | No | Human-readable label |

## Schedules

Schedules define time-based targets for each environment.

```json
"schedules": {
  "veg": {
    "phases": [
      {
        "name": "lights_on",
        "start": "06:00",
        "end": "00:00",
        "targets": {
          "temperature": { "min": 75, "max": 82, "ideal": 78 },
          "humidity": { "min": 55, "max": 70, "ideal": 62 }
        },
        "devices": {
          "veg_light": "on"
        }
      },
      {
        "name": "lights_off",
        "start": "00:00",
        "end": "06:00",
        "targets": {
          "temperature": { "min": 65, "max": 75, "ideal": 70 },
          "humidity": { "min": 55, "max": 70, "ideal": 62 }
        },
        "devices": {
          "veg_light": "off"
        }
      }
    ]
  },
  "seedling_pod": {
    "phases": [
      {
        "name": "always",
        "start": "00:00",
        "end": "23:59",
        "targets": {
          "temperature": { "min": 75, "max": 82, "ideal": 78 },
          "humidity": { "min": 65, "max": 80, "ideal": 72 }
        }
      }
    ]
  }
}
```

**Target fields:**

| Field | Required | Description |
|---|---|---|
| `min` | Yes | Below this, cost rises steeply. Solver actively works to raise. |
| `max` | Yes | Above this, cost rises steeply. Solver actively works to lower. |
| `ideal` | No | Cost is zero here. Solver drifts toward this when resources allow. |

The solver uses a continuous cost function shaped by these values and the absolute limits from the safety config. Cost is zero at ideal, rises gently between ideal and min/max, then rises steeply between min/max and the absolute limits. This means the solver naturally prioritizes environments in distress over environments that are merely suboptimal — no priority rankings needed.

**Device overrides in schedules:**

The `devices` field in a phase forces specific device states regardless of the solver. Lights follow a schedule — the solver doesn't decide when lights are on. But the solver accounts for the heat the lights generate.

## Safety

Safety configuration defines the absolute limits that the safety monitor enforces.

```json
"safety": {
  "environments": {
    "veg": {
      "limits": {
        "temperature": { "absolute_min": 45, "absolute_max": 100 },
        "humidity": { "absolute_min": 15, "absolute_max": 95 }
      },
      "rate_of_change": {
        "temperature": { "max_per_minute": 2.0 }
      }
    },
    "seedling_pod": {
      "limits": {
        "temperature": { "absolute_min": 55, "absolute_max": 95 },
        "humidity": { "absolute_min": 20, "absolute_max": 95 }
      },
      "rate_of_change": {
        "temperature": { "max_per_minute": 1.5 }
      }
    }
  },
  "devices": {
    "veg_heater": {
      "safe_state": "off",
      "coherence_window_seconds": 300,
      "max_continuous_runtime_minutes": 60
    },
    "pod_heater": {
      "safe_state": "off",
      "coherence_window_seconds": 180,
      "max_continuous_runtime_minutes": 30
    },
    "exhaust_fan": {
      "safe_state": "on",
      "coherence_window_seconds": 120
    }
  },
  "sensor_stale_after_missed": 3,
  "safety_loop_interval_seconds": 15
}
```

**Environment safety fields:**

| Field | Description |
|---|---|
| `limits` | Hard boundaries. Crossing these triggers immediate safe-state for all devices in the environment. These are NOT targets — they are emergency walls. |
| `rate_of_change` | Maximum acceptable change rate. Exceeding this suggests hardware failure, not normal operation. |

**Device safety fields:**

| Field | Description |
|---|---|
| `safe_state` | What state this device should be forced to during a safety event |
| `coherence_window_seconds` | How long after a command before the safety monitor expects to see the corresponding sensor effect |
| `max_continuous_runtime_minutes` | Maximum time a device can run without interruption. Prevents stuck-on scenarios. After this limit, the device is cycled off briefly and the safety monitor verifies sensor response before allowing it back on. |
| `hardware_countdown_seconds` | For devices that support hardware timers: how long the countdown should be. The daemon refreshes this on every control cycle. If the daemon dies, the device enforces its own safe state after this many seconds. Typically set to 2-3x the control cycle interval. |

**Global safety fields:**

| Field | Description |
|---|---|
| `sensor_stale_after_missed` | Number of consecutive missed polls before a sensor is marked stale |
| `safety_loop_interval_seconds` | How often the safety monitor runs its evaluation loop |

## Units

All temperatures in the config are Fahrenheit. All humidity values are relative humidity (%). Spriggler converts internally as needed for physics calculations.

The user works in the units they think in. The physics model works in whatever units the equations require. Conversion is Spriggler's problem, not the user's.

## Validation

On startup, Spriggler validates the config:

- All sensor and device IDs referenced in environments, schedules, and safety exist
- All circuit references exist
- All environment references in connections exist
- Schedule phases cover 24 hours without gaps
- Safety limits are wider than schedule target ranges (absolute_min < min, absolute_max > max)
- No circular environment connections
- At least one sensor per environment
- Required fields present

Validation errors are fatal. Spriggler will not start with an invalid config.

**Required ambient sensor:** At least one sensor must be assigned to environment `"ambient"`. Without ambient data, the physics model cannot function.

---
