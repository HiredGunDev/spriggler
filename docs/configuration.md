# Spriggler Configuration Schema Documentation

## Overview

The Spriggler Configuration Schema defines the structure for configuring a grow control system, including environments, sensors, devices, circuits, schedules, and alerts. It ensures consistency, modularity, and scalability.

This documentation explains the schema fields, relationships, and validation requirements, with a focus on accessibility for both technical and non-technical users.

---

## Top-Level Structure

The configuration consists of the following sections:

- **header**: Provides metadata about the configuration file.
- **environments**: Defines physical spaces and their properties.
- **sensors**: Details sensors used for monitoring.
- **devices**: Describes devices (e.g., heaters, fans) in the system.
- **circuits**: Specifies power circuits for devices.
- **schedules**: Controls operational behavior.
- **alerts**: Defines conditions for triggering notifications.
- **runtime**: Optional runtime tuning for loop cadence and controller safety.

Each section supports:

- **defaults**: Shared attributes for all definitions.
- **definitions**: Individual instances with optional overrides.

---

## Header

### Description

The `header` section provides metadata about the configuration file, such as its name, version, description, and author information. It also includes timestamps for creation and modification, as well as an optional field for upstream server configuration.

### Schema

- **signature** (string, required): A fixed identifier to validate the configuration file as a Spriggler configuration (e.g., `"SprigglerConfig"`).
- **version** (string, required): The configuration schema version.
- **name** (string, optional): A descriptive name for the configuration.
- **description** (string, optional): A brief description of the configuration's purpose.
- **author** (string, required): The user token or identifier for the configuration creator (e.g., `igrow420`).
- **created** (string, optional): The creation timestamp of the configuration in ISO 8601 format.
- **modified** (string, optional): The last modification timestamp of the configuration in ISO 8601 format.
- **upstream** (object, required): Configuration for the upstream server.
    - **url** (string, required): The personalized URL of the upstream server (e.g., `<author>.spriggle.diy/api`).
    - **auth_token** (string, optional): Authentication token for uploading logs or status.

### Notes on `auth_token`

- Spriggler checks the environment for an `SPRIGGLE_AUTH_TOKEN`. If present, it overrides the token in the configuration file.
- If no token is available in the environment or the configuration, a warning is issued, and uploads are disabled.

### Example

```json
"header": {
"signature": "SprigglerConfig",
"version": "1.0",
"name": "spriggler_configuration",
"description": "Configuration for a 4-environment grow shed",
"author": "igrow420",
"created": "2025-01-01T12:00:00Z",
"modified": "2025-01-02T08:00:00Z",
"upstream": {
"url": "https://igrow420.spriggle.diy/api",
"auth_token": "abcd1234token"
}
}
```

---

## Runtime

### Description

`runtime` settings control how often Spriggler loops, how frequently it emits heartbeats, and whether control decisions should
be executed or logged as a dry run.

### Schema

- **loop_interval_seconds** (number, optional): Delay between control loop cycles. Defaults to `1.0` seconds.
- **heartbeat_interval_seconds** (number, optional): Interval between heartbeat log entries. Defaults to `5.0` seconds.
- **debounce_seconds** (number, optional): Minimum time between repeated commands to the same device. Defaults to `5.0` seconds.
- **dry_run** (boolean, optional): When `true`, actuator commands are logged but not executed.

### Example

```json
"runtime": {
  "loop_interval_seconds": 2,
  "heartbeat_interval_seconds": 10,
  "debounce_seconds": 3,
  "dry_run": true
}
```

---

## Environments

### Description

Environments represent physical spaces in the system, defining the properties they control and how those properties are monitored and adjusted.

Environments now link directly to schedules for `targets`, removing redundancy and centralizing control logic.

### Schema

- **defaults** (optional):
    - `air`: Defines default air source and exhaust.
        - `source` (string or null): Air source.
        - `exhaust` (string or null): Exhaust destination.
- **definitions** (required):
    - `id` (string, required): Unique environment identifier.
    - `name` (string, optional): Human-readable name.
    - `air` (object, optional): Overrides for `source` and `exhaust`.
    - `properties` (object, required): Defines the properties controlled in the environment.
        - Each property includes:
            - `sensors` (array of strings): IDs of the sensors measuring the property.
            - `controllers` (array of strings): IDs of devices controlling the property.
            - `schedules` (array of strings): IDs of the schedules controlling the property.

### Example

```json
"environments": {
  "defaults": {
    "air": { "source": "plenum", "exhaust": "external" }
  },
  "definitions": [
    {
      "id": "grow1",
      "name": "Grow Environment 1",
      "air": { "source": "plenum", "exhaust": "external" },
      "properties": {
        "temperature": {
          "sensors": ["temp_sensor_grow1"],
          "controllers": ["heater_grow1", "exhaust_fan_grow1"],
          "schedules": ["day_schedule_grow1", "night_schedule_grow1"]
        },
        "humidity": {
          "sensors": ["humidity_sensor_grow1"],
          "controllers": ["humidifier_grow1", "exhaust_fan_grow1"],
          "schedules": ["day_schedule_grow1", "night_schedule_grow1"]
        }
      }
    }
  ]
}
```

---

### Property, sensor, and device mapping

Every property an environment controls must connect three things:

1. **Sensors** that report the property (e.g., `temperature`). Each sensor ID listed under `properties.<property>.sensors` must
   correspond to a sensor definition with a compatible `what` value.
2. **Controllers** that can change the property. Devices listed in `properties.<property>.controllers` must declare an effect
   for the property either in their `effects` array or via the `devices.defaults.effects` entry for the device's `what`.
3. **Schedules** that define targets. Each schedule referenced in `properties.<property>.schedules` must contain a matching
   `targets.<property>` object with `min` and/or `max` values. Optional `time_range` windows (e.g., `"07:00-19:00"`) limit
   when a schedule is considered active.

During each control loop the environment controller:

- Collects the latest sensor readings for the property.
- Picks the first schedule that is active for the current time and has targets for the property.
- Compares the reading to the schedule's `min`/`max` bounds and issues `turn_on` or `turn_off` commands to controller devices
  based on their declared effect (`increase` or `decrease`). Devices receive a command at most once every
  `runtime.debounce_seconds` seconds to avoid rapid toggling.

---

## Sensors

### Description

Sensors provide data on environmental properties and are referenced by environments.

### Schema

- **defaults** (optional):
    - `config`: Default sensor configuration.
        - `refresh_rate` (integer): Polling frequency in seconds.
- **definitions** (required):
    - `id` (string, required): Unique sensor identifier.
    - `what` (string, required): What the sensor measures (e.g., `temperature`, `humidity`).
    - `how` (string, required): Specific implementation or model of the sensor (e.g., `Govee_H5100_temperature`).
    - `config` (object, optional): Configuration details for communication and device identification.
        - **identifier** (string, required): The last 4 hexadecimal digits identifying the BLE device.
        - **address** (string, optional): A specific BLE address for the device (e.g., `ble://6F2B46BC-F97A-DE63-A7B2-3A684A6E6DC1`).
        - **refresh_rate** (integer, optional): Polling frequency in seconds.

### Example

```json
"sensors": {
  "definitions": [
    {
      "id": "temp_sensor_grow1",
      "what": "temperature",
      "how": "Govee_H5100_temperature",
      "config": {
        "identifier": "ABCD",
        "address": "ble://6F2B46BC-F97A-DE63-A7B2-3A684A6E6DC1",
        "refresh_rate": 30
      }
    },
    {
      "id": "humidity_sensor_grow1",
      "what": "humidity",
      "how": "Govee_H5100_humidity",
      "config": {
        "identifier": "1234",
        "refresh_rate": 30
      }
    }
  ]
}
```

### Govee H5100 advertisement modules

- `Govee_H5100_temperature` listens for BLE advertisements and exposes
  temperature, humidity, and battery values decoded from the payload.
- `Govee_H5100_humidity` mirrors the advertisement workflow but focuses on
  humidity and battery reporting. It can be configured with the same
  `identifier` and optional `address` fields shown above.

---

## Devices

### Description

Devices are physical components (e.g., heaters, fans) that control environmental properties. Each device must define its effects on properties.

### Schema

- **defaults** (optional):
    - `power`: Default power configuration (e.g., circuit, rating).
    - `effects`: Default effects for devices based on their `what` values.
        - Each effect includes:
            - `property` (string): The property affected by the device (e.g., `temperature`, `humidity`).
            - `type` (string): The type of effect (`increase`, `decrease`, `dynamic_effect`).
- **definitions** (required):
    - `id` (string, required): Unique device identifier.
    - `what` (string, required): What the device controls or modifies (e.g., `heater`, `fan`).
    - `how` (string, required): Specific implementation or model of the device (e.g., `KASA_Device`).
    - `power` (object, optional): Circuit and power rating for the device.
    - `control` (object, required): Communication protocol and properties.
        - **name** (string, optional): User-defined name for the device (e.g., configured in the KASA app or CLI). If provided, discovery will be performed.
        - **outlet_name** (string, optional): Name for the specific outlet on the device (for multi-outlet devices).
        - **ip_address** (string, optional): Direct IP address for TCP/IP connections (used if `name` is not provided).
        - **port** (integer, optional): Port number for direct connections (used if `name` is not provided).
    - `effects` (array, optional): Overrides default effects for the device.

### TP-Link KASA Powerbar (`KASA_Powerbar`)

The `KASA_Powerbar` device driver controls individual outlets on TP-Link KASA smart power strips using the [`python-kasa`](https://python-kasa.readthedocs.io/) library. The driver validates configuration at startup to ensure it can target the correct outlet.

- **Dependencies**: Install `python-kasa` with `pip install python-kasa`.
- **Required fields**:
    - `control.outlet_name`: Must match the outlet alias defined in the KASA app. Matching is case-insensitive.
    - Either `control.name` (the device alias used for auto-discovery) **or** `control.ip_address` (a static IP address). If discovery is unavailable or unreliable, provide the IP address and optional `control.port` (defaults to `9999`).
- **Metadata**: Upon initialization, Spriggler records the resolved host, port, and the list of available outlets. This information appears in logs and diagnostic endpoints.
- **Troubleshooting**:
    - Run `kasa discover` to confirm the strip is reachable when using discovery by name.
    - Verify the outlet aliases in your configuration match those displayed in the KASA app.
    - Assign a static IP and set `control.ip_address` if the strip is on a different VLAN or discovery is blocked by network policies.

### Example

```json
"devices": {
  "defaults": {
    "power": { "circuit": "default_circuit", "rating": 1500 },
    "effects": {
      "heater": [{ "property": "temperature", "type": "increase" }],
      "fan": [
        { "property": "temperature", "type": "dynamic_effect" },
        { "property": "humidity", "type": "dynamic_effect" }
      ],
      "humidifier": [{ "property": "humidity", "type": "increase" }],
      "light": [{ "property": "illumination", "type": "state" }]
    }
  },
  "definitions": [
    {
      "id": "heater_grow1",
      "what": "heater",
      "how": "KASA_Device",
      "power": { "circuit": "circuit_grow1", "rating": 1500 },
      "control": {
        "name": "seedling",
        "outlet_name": "Heater"
      }
    },
    {
      "id": "exhaust_fan_grow1",
      "what": "fan",
      "how": "Custom_Controller",
      "power": { "circuit": "circuit_grow1", "rating": 500 },
      "control": {
        "name": "seedling",
        "outlet_name": "Fan"
      },
      "effects": [
        { "property": "temperature", "type": "dynamic_effect" },
        { "property": "humidity", "type": "dynamic_effect" }
      ]
    },
    {
      "id": "tcp_light",
      "what": "light",
      "how": "TCP_Device",
      "power": { "circuit": "circuit_grow2", "rating": 100 },
      "control": {
        "ip_address": "192.168.1.15",
        "port": 8888
      }
    }
  ]
}
```

---

## Circuits

### Description

Circuits define power sources for devices.

### Schema

- **defaults** (optional):
    - `max_load` (integer): Default maximum power capacity (watts).
- **definitions** (required):
    - `id` (string, required): Unique circuit identifier.
    - `name` (string, optional): Human-readable name.
    - `max_load` (integer, optional): Overrides for power capacity.

### Example

```json
"circuits": {
  "defaults": { "max_load": 1800 },
  "definitions": [
    {
      "id": "circuit_grow1",
      "name": "15A Circuit for Grow Room 1"
    }
  ]
}
```

---

## Schedules

### Description

Schedules define dynamic behavior for environments and devices, including operational time ranges and property-specific targets. Schedules now exclusively handle `targets`, centralizing control logic and avoiding redundancy with environment definitions.

Illumination does not rely on a light sensor; the system assumes on/off commands issued to KASA-controlled outlets achieve the desired state.

### Schema

- **defaults** (optional):
    - `time_range`: Specifies default operational time ranges.
- **definitions** (required):
    - `id` (string, required): Unique schedule identifier.
    - `time_range` (string, required): Active time range (e.g., "06:00-18:00").
    - `targets` (object): Property-specific targets for the scheduled time range.

### Example

```json
"schedules": {
    "defaults": {
      "time_range": "06:00-18:00"
    },
    "definitions": [
            {
                "id": "day_schedule_grow1",
                "time_range": "06:00-18:00",
                "targets": {
                    "temperature": { "min": 70, "max": 75 },
                    "humidity": { "min": 50, "max": 60 },
                    "illumination": "on"
                }
        },
            {
                "id": "night_schedule_grow1",
                "time_range": "18:00-06:00",
                "targets": {
                    "temperature": { "min": 65, "max": 70 },
                    "humidity": { "min": 55, "max": 65 },
                    "illumination": "off"
                }
        }
    ]
}
```

---

## Alerts

### Description

Alerts define conditions for monitoring and notifying when properties deviate from acceptable ranges.

### Schema

- **defaults** (optional):
    - `severity`: Default severity level (e.g., "warning", "critical").
- **definitions** (required):
    - `id` (string, required): Unique alert identifier.
    - `condition` (object, required): Specifies the triggering condition.
        - **sensor** (string, required): ID of the sensor being monitored.
        - **operator** (string, required): Comparison operator (e.g., ">", "<", "=").
        - **value** (number, required): Threshold value.
    - `message` (string, required): Notification message.
    - `severity` (string, optional): Overrides the default severity level.

### Example

```json
"alerts": {
"defaults": {
"severity": "warning"
},
"definitions": [
{
"id": "high_temp_alert",
"condition": {
"sensor": "temp_sensor_grow1",
"operator": ">",
"value": 80
},
"message": "Temperature exceeds 80°F in Grow Room 1.",
"severity": "critical"
}
]
}
```

---

