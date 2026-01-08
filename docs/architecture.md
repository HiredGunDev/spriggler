# Spriggler Architecture

## Version

0.3 - Physics Solver

## Overview

Spriggler is a daemon that controls environmental systems (grow rooms, greenhouses, aquaculture, fermentation) by solving for optimal device states rather than reacting to threshold crossings.

The core insight: environmental control is a thermodynamics problem. Temperature, humidity, and airflow are governed by known physics. Rather than configuring complex rules about what each device does, we *measure* what each device does during calibration, then let a solver find the optimal combination of device states to reach target conditions with minimum energy.

## Design Principles

**Local-only.** All data stays on the device. No cloud, no accounts, no telemetry. Users are growing plants that may be legally sensitive. Their data is theirs.

**Explainable decisions.** Every action the system takes can be traced to measured coefficients and solved constraints. "Why did you turn on the fan?" has a real answer, not a black box.

**Learn, don't configure.** Users describe what exists (sensors, devices, environments). The system learns what things *do* through calibration. No manual entry of watts, CFM, or transfer coefficients.

**Property-agnostic, physics-aware.** The solver doesn't know "temperature" from "pH" - they're all just properties with values and targets. But the physics model knows how properties interact (e.g., heating air changes relative humidity).

**Fail toward safety.** When the model diverges from reality or constraints become unsatisfiable, the system must act conservatively and alert the user, not freeze or crash.

## System Components

```
┌─────────────────────────────────────────────────────┐
│                     CLI / Web UI                     │
├─────────────────────────────────────────────────────┤
│            config/    calibration/    logs/          │
├─────────────────────────────────────────────────────┤
│                   SPRIGGLER DAEMON                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Sensors  │ │ Devices  │ │ Physics  │ │ Solver │  │
│  │ Drivers  │ │ Drivers  │ │  Model   │ │        │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘  │
└─────────────────────────────────────────────────────┘
         │              │
         ▼              ▼
   [BLE, WiFi]    [KASA, VeSync, etc.]
```

### Sensors

Sensor drivers read physical state. Each driver knows how to talk to specific hardware (Govee BLE thermometers, etc.) and exposes a uniform interface: `read() → dict of property values`.

Sensors report what they measure. They don't know what properties mean.

### Devices

Device drivers control physical equipment. Each driver knows how to talk to specific hardware (KASA smart plugs, VeSync humidifiers, etc.) and exposes a uniform interface: `turn_on()`, `turn_off()`, `is_on()`, `get_power()`.

Devices that support power monitoring (KASA) report real-time wattage. This feeds both calibration and runtime optimization.

### Physics Model

The physics model contains:

1. **Known equations** - Psychrometrics, heat transfer, mass balance. These are code.

2. **Learned coefficients** - Transfer functions for each device in each environment. These come from calibration.

The model can answer: "If I turn on device X, what happens to properties Y and Z over time?"

### Solver

The solver is a constrained optimizer. Given:

- Current state (all sensor readings)
- Target state (from schedule/config)
- Learned model (what each device does)
- Constraints (circuit amperage limits, device conflicts)

It finds: the combination of device states that moves toward targets with minimum energy expenditure.

This is not machine learning. It's solving a system of equations with known physics and measured coefficients.

## Data Flow

### Normal Operation

```
1. Sensors report current state
2. Scheduler provides current targets
3. Solver computes optimal device states given:
   - Current state
   - Target state  
   - Learned transfer functions
   - Constraints (electrical, physical)
4. Devices receive commands
5. Validation: predicted vs actual state compared
6. Log decisions with full explanation
```

### Calibration

```
1. User initiates calibration (environment empty)
2. System measures baseline (all devices off, ambient drift)
3. For each device:
   a. Turn on device
   b. Record sensor changes over time
   c. Record power consumption (if available)
   d. Compute transfer coefficients
   e. Turn off device, wait for settling
4. Store learned model in calibration/
5. Validate model with spot checks
```

## File Interfaces

### config/

User-authored. Describes what exists:

- **Environments** - Physical spaces, their connections (air source/sink)
- **Sensors** - What hardware, where it reports, which environment
- **Devices** - What hardware, how to control it, which environment, which circuit
- **Circuits** - Electrical capacity limits
- **Schedules** - Time-based targets for each environment

Config does NOT describe what devices do to properties. That's learned.

### calibration/

System-generated during calibration. Contains:

- **Baseline measurements** - Ambient drift rates per environment
- **Device transfer functions** - Measured effect of each device on each property
- **Power profiles** - Actual wattage under load
- **Calibration metadata** - When calibrated, ambient conditions at time

Calibration is environment-specific. Recalibrate when physical setup changes.

### logs/

System-generated during operation:

- **Decisions** - What the solver chose and why
- **Predictions vs actuals** - Model validation
- **Anomalies** - When reality diverges from model
- **Events** - Device commands, sensor readings, errors

Structured format for machine parsing. Human-readable for debugging.

## Cross-Environment Optimization

Environments can exchange resources:

- **Heat** - Inter-chamber fans move warm/cool air
- **Humidity** - Moving air moves moisture
- **Electrical capacity** - Devices on different circuits

When Environment A needs something that Environment B has excess of, the solver may choose transfer over generation. Example:

- Veg needs heat
- Flower is above target temperature  
- Inter-chamber fan costs 23W
- Veg heater costs 1500W
- Solver chooses: fan

This emerges from the optimization, not from coded rules.

## Electrical Constraints

Circuits have amperage limits. The solver treats electrical capacity as a constraint alongside thermodynamic targets.

If running Device A would exceed circuit capacity, the solver looks for alternatives - possibly running an equivalent device on a different circuit, or achieving the goal indirectly through environmental transfer.

## Failure Modes

### Model Drift

When predicted state diverges from actual state beyond threshold:

1. Log the discrepancy with details
2. Continue operating with reduced confidence
3. Alert user: "Model accuracy degraded, consider recalibration"

### Unsatisfiable Constraints

When targets cannot be reached with available devices:

1. Get as close as possible
2. Log which constraints are violated and why
3. Alert user: "Cannot reach target humidity, all available devices at capacity"

### Hardware Failure

When a device or sensor stops responding:

1. Mark it unavailable
2. Re-solve without that resource
3. Alert user
4. Continue with degraded capability

### Calibration Stale

When calibration data is old or ambient conditions differ significantly from calibration conditions:

1. Reduce model confidence
2. Widen acceptable prediction error
3. Suggest recalibration

## What Carries Forward from 0.2

**Keep (with review):**

- Sensor drivers (Govee BLE)
- Device drivers (KASA, VeSync)
- Power state management
- Structured logging format

**Discard:**

- Influence model
- Policy-based effects
- Per-property evaluation
- Sequential command issuing

## Open Questions

- Solver implementation: off-the-shelf optimizer or custom?
- Calibration granularity: how many data points per device?
- Psychrometric library: existing package or roll our own?
- Schedule format: same as 0.2 or revised?
- Web UI protocol: REST API, WebSocket, or pure file-based?

---


