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

**Hardware-level failsafes.** When devices support independent timers, keep-alives, or countdown mechanisms, Spriggler uses them to enforce safe states at the hardware level. For example, a KASA plug powering a heater will have its countdown timer set to turn OFF. Spriggler refreshes this timer on every control cycle. If Spriggler crashes, loses network, or the host machine dies, the plug counts down and turns itself off without any software intervention. The safe state is enforced by the hardware, not by the daemon. Software safety is defense in depth — hardware safety is the last line.

**Sensors are truth. Devices lie.** Device self-reported status is informational, not authoritative. A heater that claims to be on while temperature drops is a failed heater. Safety decisions are always based on sensor readings, never on device state alone.

## Executables

Spriggler has two entry points with a clean separation of concerns:

**`spriggler-daemon`** — the daemon. Runs forever, no interactivity. Reads config, reads calibration data, runs the control loop, writes status and structured log to the state directory. The only communication it accepts is a changed config file (detected by mtime check each cycle) and SIGTERM to stop.

```
spriggler-daemon --config config.json [--state-dir DIR] [--quiet] [--verbose]
```

**`spriggler <command>`** — the CLI. Everything else:

```
spriggler display [--interval N]          Live curses dashboard (implemented)
spriggler calibrate [--device <id>]       Run calibration experiments (planned)
spriggler check                           Validate config, exit (planned)
spriggler status                          Pretty-print status.json (planned)
spriggler explain [--cycle <n>]           Explain a solver decision from logs (planned)
```

Both accept `--state-dir` to override the state directory (default: `~/.spriggler/`).

The daemon and CLI share the same libraries (config loader, drivers, physics model, units) but have completely different control flow. The daemon is a loop. The CLI is interactive commands.

**No IPC.** The daemon and CLI never communicate directly. All coordination is through the filesystem — specifically the state directory. This means a UI crash cannot take down the daemon. It also means any tool that can read and write files can interact with Spriggler — vim, a web UI, a cron job, a shell script.

## System Components

```
┌────────────────────────────────────────────────────────────┐
│                   spriggler <command>                       │
│       display │ calibrate │ status │ explain                │
├───────────────┼────────────────────────────────────────────┤
│               │ reads                                      │
│               ▼                                            │
│  ┌─────────────────────┐                                   │
│  │  ~/.spriggler/      │  ← State directory                │
│  │    status.json      │    (--state-dir override)         │
│  │    spriggler.log    │                                   │
│  │    calibration/     │                                   │
│  └──────────┬──────────┘                                   │
│             │ writes         ┌──────────┐                  │
│             │                │ config/  │                  │
│             │                │  .json   │                  │
│             │                └────┬─────┘                  │
│             │                     │ reads                   │
│             ▼                     ▼                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                SPRIGGLER-DAEMON                      │   │
│  │                                                      │   │
│  │  ┌────────────────────────────────────────────────┐  │   │
│  │  │              SAFETY MONITOR                     │  │   │
│  │  │  Independent loop. Reads sensors. Vetoes.       │  │   │
│  │  │  Cannot be overridden by solver.                │  │   │
│  │  └────────────────────────────────────────────────┘  │   │
│  │                                                      │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │   │
│  │  │ Sensors  │ │ Devices  │ │ Physics  │ │ Solver │  │   │
│  │  │ Drivers  │ │ Drivers  │ │  Model   │ │        │  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────┘  │   │
│  │                                                      │   │
│  │  ┌──────────┐ ┌──────────┐                           │   │
│  │  │Structured│ │  State   │                           │   │
│  │  │  Logger  │ │  Writer  │                           │   │
│  │  └──────────┘ └──────────┘                           │   │
│  └─────────────────────────────────────────────────────┘   │
│           │              │                                  │
│           ▼              ▼                                  │
│     [BLE, WiFi]    [KASA, VeSync, etc.]                    │
└────────────────────────────────────────────────────────────┘
```

### Safety Monitor

The safety monitor is a first-class component, separate from and superior to the solver. It runs on its own loop, reads sensors directly, and has authority to override any device command.

**Why it's separate:** The solver optimizes. It finds the best path to targets. But optimization has no concept of "this will destroy what's in this environment." The safety monitor does. A solver might decide a heater should run because the model says the environment is cold. The safety monitor knows the sensor reads 130°F and kills the heater regardless of what the solver or the device itself claims.

**What it monitors:**

- **Absolute limits.** Hard boundaries that must never be crossed. These are not targets — they are walls. The user defines them based on what's in the environment.
- **Rate of change.** A 20°F spike in 5 minutes is a hardware failure, not weather. Rapid change in any property triggers investigation and possible shutdown.
- **Device-sensor coherence.** If a device claims ON and the expected sensor effect doesn't appear within a reasonable window, the device is presumed failed. If a device claims OFF and the sensor shows its signature effect, the device is presumed stuck on.
- **Sensor liveness.** If a sensor stops reporting, the safety monitor cannot verify safety. Affected devices enter safe state until sensor data resumes.
- **Sensor battery.** If a sensor reports a `battery` value, the safety monitor tracks it. Warning at 20% (default). Critical alert at 5% (default). A dead battery is a predictable sensor failure — the safety monitor warns before it becomes a sensor liveness event that forces devices to safe state.
- **Sensor signal strength.** If a sensor reports an `signal_strength` (RSSI) value, the safety monitor tracks it. Degrading signal predicts missed polls and eventual sensor stale events. Warning when RSSI drops below threshold (default: -90dBm). Trending RSSI decline over days suggests battery degradation, physical obstruction, or equipment relocation — all worth alerting on before the sensor goes dark.

**What it can do:**

- Force any device to its configured safe state (typically OFF for heaters, ON for exhaust fans)
- Lock out a device entirely (prevent solver from using it)
- Escalate alerts (log, notification, alarm)

**What it cannot do:**

- Turn on a device that the solver hasn't requested. It vetoes; it doesn't command.
- Be overridden by the solver. Safety trumps optimization, always.

**Safe states are per-device and configured by the user:**

| Device Type | Typical Safe State | Rationale |
|---|---|---|
| Heater | OFF | Runaway heater causes damage or fire |
| Exhaust fan | ON | Ventilation prevents heat/humidity buildup |
| Humidifier | OFF | Excess humidity causes damage |
| Light | Current state | Lights don't pose immediate environmental danger |
| Circulation fan | ON | Air movement is generally safe |

### Sensors

Sensor drivers read physical state. Each driver knows how to talk to specific hardware (Govee BLE thermometers, etc.) and exposes a uniform interface: `read() → dict of property values`.

Sensor drivers read physical hardware and return measurements. Each driver returns a dict with all values the hardware reports, in SI units. The keys follow a standard taxonomy (`temperature`, `humidity`, `battery`, etc.).

```python
# Example return from a Govee H5100 driver
{
    "temperature": 295.37,   # Kelvin
    "humidity": 63.2,        # %RH (already dimensionless)
    "battery": 87            # percent
}
```

**Drivers always return SI units.** This is the conversion boundary. Drivers return SI. The daemon converts to user units (from the config's `units` block) at the display and logging boundary. The physics engine works in SI natively. If a value came from a driver, it's SI. If it's in the config or the UI, it's user units. No ambiguity.

## Unit Convention

**All internal values are SI.** Temperature is Kelvin. Humidity is %RH (already dimensionless). This is a non-negotiable design decision.

**The conversion boundary is `load_config()`.** The config file is authored in user units (°F or °C). Validation runs in user units so error messages make sense to the user. After validation, `load_config()` converts all temperature values to Kelvin. Everything downstream — physics model, solver, safety monitor, calibration data, stored logs — operates in SI exclusively.

**Why this matters:** Calibration data must survive a unit change. If a user switches their config from °C to °F (or sells the system to someone who does), all learned coefficients, envelope conductance values, and device contributions remain valid because they were never stored in user units.

**What gets converted at load time:**
- Schedule targets (min, max, ideal) for temperature
- Safety limits (absolute_min, absolute_max) for temperature
- Rate of change thresholds for temperature

**What does NOT get converted:**
- Humidity (%RH is dimensionless)
- Battery (percent)
- Signal strength (dBm)
- Calibration data (always SI)
- Log data on disk (always SI)

**Display surfaces convert back for humans:**
- Log messages: `format_temp(297.04, 'F')` → `"75.0 F"`
- Alerts: always include the unit label
- Any future web UI or dashboard

The original user unit is preserved in `config['_original_unit']` for display formatting. The unit label always travels with the displayed value. Never "75.0" alone — always "75.0 F".

**Drivers return everything the hardware reports.** The driver doesn't consult the config to decide what to return — it returns the full dict. The daemon routes properties listed in the sensor's `properties` field to the environment model and solver. Properties not in anyone's target list or safety limits (like battery) flow through to logs, diagnostics, and the safety monitor. If the hardware starts reporting a new property, it appears in logs automatically.

**The ambient sensor is required.** Without knowing the outside temperature and humidity, the physics model cannot compute envelope loss rates, which means it cannot predict the net effect of any device. Place the ambient sensor outside of all controlled environments — outdoors, or in the room that contains a grow tent.

Sensors report what they measure. They don't know what properties mean.

**Sensor health tracking:** The safety monitor tracks each sensor's reporting interval. If a sensor misses more than N consecutive expected reports, it is marked stale. Environments with stale sensors trigger conservative device management.

### Devices

Device drivers control physical equipment. Each driver knows how to talk to specific hardware (KASA smart plugs, VeSync humidifiers, etc.) and exposes a uniform interface: `turn_on()`, `turn_off()`, `is_on()`, `get_power()`.

Devices that support power monitoring (KASA) report real-time wattage. This feeds both calibration and runtime optimization.

**Hardware failsafe timers:** Drivers that support hardware-level countdown timers expose: `set_countdown(seconds, target_state)` and `supports_countdown() → bool`. During normal operation, the daemon sets a countdown to safe state on every control cycle. If the daemon stops refreshing, the hardware enforces the safe state autonomously. This is the most important safety feature in the system — it works even when everything else has failed.

**Device status is advisory, not authoritative.** The system always validates device behavior against sensor readings. A heater reporting ON with no temperature change is flagged. A heater reporting OFF with rising temperature is flagged.

### Manual Override

The daemon queries actual device state each cycle. If a device's state differs from what the daemon last commanded, one of two things happened: the device malfunctioned, or a human pressed the button.

The `manual_override_minutes` field in the device config controls the response:

- **Missing or 0:** The daemon owns this device absolutely. State mismatch triggers an immediate correction command and a log entry. This is appropriate for heaters, exhaust fans, and any device where unexpected state changes are dangerous or undesirable.
- **Positive value:** The daemon recognizes the mismatch as a human override. It starts a hold timer for N minutes, logs the override, and the solver treats the device as forced to whatever state the human set. When the timer expires, the solver resumes control. This is appropriate for lights, circulation fans, and other devices where brief manual control is normal and safe.

The safety monitor has veto authority over manual overrides. If a manually-overridden device causes a safety limit breach, safety kills it regardless.

Repeated state mismatch corrections on a device with `manual_override_minutes: 0` are a diagnostic signal — the command isn't sticking (network issue), the device is malfunctioning, or someone is pressing buttons they shouldn't be. All worth logging and potentially alerting on.

```json
"chamber_light": {
"manual_override_minutes": 30
},
"chamber_heater": {
"manual_override_minutes": 0
}
```

No CLI or computer access is needed to perform a manual override. Walk up to the device, press the button, and the daemon notices and backs off.

### Physics Model

The physics model has two layers:

**1. Envelope model (learned per environment):**

Every environment loses energy to its boundary conditions — the space outside its walls. A shed loses heat to the outdoors. A grow tent inside a shed loses heat to the shed. The rate of loss is proportional to the temperature differential between inside and outside.

During calibration (all devices off), the system measures how fast each environment drifts toward ambient. This gives the thermal loss coefficient of the space — how leaky the envelope is. This coefficient, combined with the *current* ambient temperature, lets the model predict how fast the environment will cool (or warm) on its own.

This is why the ambient sensor is not optional. Without knowing the outside temperature, the model cannot predict thermal loss, which means it cannot predict the net effect of any device.

**2. Device model (learned per device):**

Each device contributes energy to (or removes energy from) the environment. A 1500W heater delivers roughly 1500W of thermal energy regardless of outdoor temperature. What *changes* with outdoor temperature is the *net* effect — the device's contribution minus the envelope's loss.

During calibration, the system measures each device's contribution *on top of* the baseline envelope loss. The learned value is the device's energy contribution rate, not the net temperature change. The net change is computed at runtime using the current ambient conditions.

**The model answers:** "Given current indoor temperature, current ambient temperature, and the envelope loss rate, if I turn on device X, what will the net effect on properties Y and Z be over time?"

At 80°F outside with an indoor target of 78°F, the heater barely needs to run — envelope loss is minimal. At 5°F outside, the heater runs constantly and may not keep up — envelope loss exceeds heater capacity. The solver knows this because it has both numbers.

**Known equations** — Psychrometrics (via PsychroLib), heat transfer, mass balance. These are code.

**Learned coefficients** — Envelope loss rates per environment, device energy contributions, interaction effects (e.g., lights generating heat). These come from calibration.

**Interaction awareness:** The model knows that:
- Heating air reduces relative humidity (psychrometrics)
- Moving air between spaces transfers both heat and moisture
- Lights generate heat as a side effect
- Exhaust fans remove both heat and humidity
- These interactions are physical law, not learned — but their magnitudes in specific environments are learned

### Solver

The solver is a constrained optimizer. Given:

- Current state (all sensor readings)
- Target state (from schedule/config)
- Learned model (what each device does)
- Constraints (circuit amperage limits, device conflicts)

It finds: the combination of device states that moves toward targets with minimum energy expenditure.

This is not machine learning. It's solving a system of equations with known physics and measured coefficients.

The solver proposes. The safety monitor disposes.

## Data Flow

### Normal Operation

```
1. Sensors report current state
2. Safety monitor evaluates: any absolute limits breached?
   - YES → Force safe states, alert, skip to step 6
   - NO  → Continue
3. Scheduler provides current targets
4. Solver computes optimal device states given:
   - Current state
   - Target state
   - Learned transfer functions
   - Constraints (electrical, physical)
   - Device lockouts (from safety monitor)
5. Safety monitor reviews proposed commands:
   - Would this command violate any safety rule? → Veto it
   - All clear? → Execute
6. Devices receive commands
7. Validation: predicted vs actual state compared
   - Safety monitor: device-sensor coherence check
   - Solver: model accuracy tracking
8. Log decisions with full explanation
```

### Calibration

Calibration derives physical constants of the system — not snapshots of behavior at one point in time. The goal is to characterize properties that remain valid across seasons and conditions.

Calibration is run by `spriggler calibrate`, not the daemon. The daemon should be stopped during calibration — two processes commanding the same hardware is a recipe for confusion. The calibration tool checks for a running daemon and warns if one is detected.

#### Why Active, Not Passive

The original design called for 48 hours of passive observation with all devices off, watching the environment drift toward ambient. This doesn't work in practice. If a shed has been soaking at ambient for a week and today's ambient swing is 10 degrees, the interior drifts a few degrees over 24 hours. The signal-to-noise ratio is terrible. You're trying to fit a conductance coefficient from tiny deltas buried in sensor noise.

The solution: use the devices themselves to create signal. Turn the heater on, push the environment 30 degrees above ambient, shut it off. Now you have a 30-degree differential and an exponential decay curve back toward ambient. That curve is rich with information — the time constant gives you the envelope conductance directly. And you characterized the heater at the same time, because you watched the temperature rise while measuring power draw.

One experiment characterizes both a device and the envelope simultaneously.

#### The Calibration Experiment

For each device in an environment, `spriggler calibrate` runs:

**Rise phase (device characterization):**

1. Record ambient and interior conditions. Record starting differential.
2. Turn device on. Record power draw if hardware supports it (KASA plugs report real watts).
3. Watch the affected property change. For a heater: temperature rises. For a humidifier: humidity rises.
4. Continue until a target differential is reached or safety limits approach.
5. Record the rise curve: timestamped (property_value, ambient_value, power_watts) tuples.

**Decay phase (envelope characterization):**

1. Turn device off.
2. Watch the property decay back toward ambient. This is a clean exponential governed by the envelope conductance.
3. Continue until the property is within a few degrees of ambient or the decay rate has stabilized.
4. Record the decay curve: timestamped (property_value, ambient_value) tuples.

**What falls out of the math:**

- **Device energy contribution:** From the rise phase. How many degrees (or %RH) per unit time, at what power consumption. Corrected for concurrent envelope loss during the rise.
- **Envelope conductance:** From the decay phase. The time constant of the exponential decay, combined with the known differential, gives the aggregate thermal conductance of the space.
- **Cross-validation:** The envelope conductance derived from the decay phase should be consistent with the envelope loss observed during the rise phase (the device has to overcome envelope loss to raise the temperature). If they disagree, something nonlinear is happening.

**Duration:** A heater cycle might be 20-30 minutes up, 45-90 minutes back down depending on the envelope. A few hours characterizes every device plus the envelope, with high-signal data across a wide temperature range. Compare this to 48 hours of watching nothing drift toward nothing.

#### Device Types

**Heating/cooling devices (heaters, exhaust fans, A/C):** Straightforward rise/decay on temperature. Run device, measure rise rate and power. Shut down, measure decay.

**Humidity devices (humidifiers, dehumidifiers):** Same pattern on humidity. Run device, watch humidity change, shut down, watch decay. Moisture dynamics are slower than thermal — expect longer experiment times.

**Lights:** Lights are primarily scheduled, not solver-controlled. But they have thermal side effects. Calibration turns lights on, measures the thermal contribution over time. This lets the solver account for "lights on adds 3 degrees" when computing heating/cooling needs.

**Air movement devices (inter-environment fans):** These don't add or remove energy — they redistribute it. Calibration must watch both source and destination environments simultaneously. Turn on the veg→flower fan, watch veg temperature drop and flower temperature rise. The transfer rate depends on the differential between the two spaces. For reversible fans (`['off', 'forward', 'reverse']`), each direction is calibrated independently — duct geometry may not be symmetric.

**Graduated devices:** Each level is a separate experiment. A humidifier with `['off', 'low', 'mid', 'high']` gets three rise/decay cycles. The solver needs to know the contribution at each level, not just max.

#### Power Consumption

If the hardware reports power draw (KASA plugs do), calibration records actual watts under load for every device state. This data feeds:

- **Calibration accuracy:** Real watts vs nameplate watts. A "1500W" heater pulling 1487W is normal. One pulling 900W has a failing element — worth flagging immediately.
- **Energy cost modeling:** The solver can prefer lower-energy solutions when multiple combinations produce similar environmental outcomes.
- **Degradation tracking:** Power draw that changes over time is a diagnostic signal. A heater pulling 15% less than at calibration suggests element degradation.

#### Calibration Output

Each device gets a JSON file in `calibration/`. All values in SI.

```
calibration/
  chamber_heater.json
  chamber_exhaust.json
  humidifier.json
  pod_heater.json
  transfer_fan.json
  chamber_light.json
  envelope_chamber.json
  envelope_pod.json
```

**Device calibration file** (e.g., `chamber_heater.json`):

```json
{
  "device_id": "chamber_heater",
  "environment": "chamber",
  "calibrated_at": "2026-02-20T15:42:00Z",
  "ambient_during_cal": {"temperature": 283.15},
  "power_draw_watts": 1487.3,
  "effects": {
    "on": {
      "temperature": {
        "contribution_per_cycle": 2.89,
        "rise_rate_per_second": 0.048
      }
    },
    "off": {
      "temperature": {"contribution_per_cycle": 0.0}
    }
  },
  "envelope_conductance_observed": 0.047,
  "raw_data": {
    "rise_samples": 142,
    "decay_samples": 287,
    "rise_duration_seconds": 1704,
    "decay_duration_seconds": 3444
  }
}
```

**Envelope calibration file** (e.g., `envelope_chamber.json`):

```json
{
  "environment": "chamber",
  "calibrated_at": "2026-02-20T17:15:00Z",
  "conductance": {
    "temperature": 0.047,
    "humidity": 0.023
  },
  "derived_from_devices": ["chamber_heater", "chamber_exhaust"],
  "conductance_consistency": {
    "temperature": {"mean": 0.047, "std": 0.003, "max_deviation": 0.004},
    "humidity": {"mean": 0.023, "std": 0.002, "max_deviation": 0.003}
  },
  "ambient_range_observed": {
    "temperature": {"min": 281.48, "max": 285.93}
  },
  "linearity_assessment": "consistent"
}
```

The envelope file aggregates conductance observations from all device experiments. The consistency metrics tell you how much the conductance varied across experiments. Low std = good fit. High std = nonlinear behavior worth investigating.

#### Passive Recalibration (continuous, during normal operation)

After initial calibration, the system continuously validates its model against reality. Every solver prediction that can be compared to an actual outcome is a data point.

**What passive recalibration tracks:**

- **Envelope conductance stability.** The R-value shouldn't change unless something physical changed. If prediction errors trend consistently in one direction over days or weeks, the system reports: "Envelope conductance appears to have increased by 15% since calibration. Possible causes: seal deterioration, insulation displacement, new air leak. Consider physical inspection."

- **Device contribution stability.** If a heater's measured thermal output drops over time, the element may be degrading. The system flags this as a maintenance issue, not a calibration issue.

- **Model accuracy score.** Continuously computed. When accuracy drops below threshold, the system recommends action — not "recalibrate" as a blanket instruction, but specific diagnosis: "Envelope model accuracy degraded. Device models remain accurate. Physical inspection of environment seals recommended."

**What passive recalibration does NOT do:**

- Silently adjust coefficients without telling the user. If the R-value appears to have changed, that's a physical event worth investigating, not a number to quietly tweak.
- Replace initial calibration. Passive recalibration validates and diagnoses. It doesn't derive new coefficients from scratch — the initial controlled-conditions calibration provides the baseline.

**The insight:** Most environmental controllers treat recalibration as "run the setup wizard again." Spriggler treats calibration drift as diagnostic information. A changing R-value means something happened to the structure. A changing device contribution means something happened to the equipment. These are things the user needs to know, not things the software should hide.

## File Interfaces

All communication between processes is file-based. No IPC, no sockets, no REST endpoints. The daemon reads files and writes files. The CLI reads files and writes files. They never talk to each other directly.

### State Directory

The daemon writes all output to a single state directory. The CLI reads from the same directory. Default: `~/.spriggler/`. Override with `--state-dir` flag or `SPRIGGLER_STATE_DIR` environment variable.

```
~/.spriggler/
    status.json         Current state (atomic write each cycle)
    spriggler.log       Structured JSON-lines event log
    calibration/        (future) Learned physical constants
```

Resolution priority: `--state-dir` flag > `SPRIGGLER_STATE_DIR` env var > `~/.spriggler/` default.

### config/config.json

User-authored (or written by a UI). Describes what exists:

- **Environments** - Physical spaces, their connections (air source/sink)
- **Sensors** - What hardware, where it reports, which environment
- **Devices** - What hardware, how to control it, which environment, which circuit
- **Circuits** - Electrical capacity limits
- **Schedules** - Time-based targets for each environment
- **Safety** - Absolute limits per environment, per-device safe states, rate-of-change thresholds, coherence windows

Config does NOT describe what devices do to properties. That's learned.

The daemon checks config mtime each cycle. If it changes, the daemon validates the new config and swaps it in live. If validation fails, the daemon keeps the old config and logs the error to status.json. No signals or IPC needed for config reload.

### calibration/

System-generated by `spriggler calibrate`. One JSON file per device, one per environment envelope. All values in SI. Contents described in the Calibration section above.

The daemon reads calibration files at startup and after config reload. Calibration data survives a config unit change (F→C or vice versa) because it is never stored in user units.

### status.json

System-generated by the daemon. Written every cycle. Current state of the world, no history. All values in SI — unit conversion is the concern of whatever reads this file.

The `running` field enables three-state daemon health detection:
- `running: true` + fresh timestamp → daemon is running normally
- `running: false` + any timestamp → daemon shut down cleanly
- `running: true` + stale timestamp → daemon crashed or hung

```json
{
  "timestamp": "2026-02-20T15:42:44Z",
  "running": true,
  "cycle": 42,
  "config_mtime": "2026-02-20T10:00:00Z",
  "config_error": null,
  "environments": {
    "chamber": {
      "readings": {"temperature": 296.48, "humidity": 55.0},
      "targets": {
        "temperature": {"min": 295.37, "max": 300.93, "ideal": 298.15}
      },
      "safe_mode": false
    }
  },
  "devices": {
    "chamber_heater": {
      "state": "on",
      "power_watts": 1487.3,
      "runtime_seconds": 312,
      "locked_out": false,
      "manual_override": false
    }
  },
  "sensors": {
    "chamber_sensor": {
      "stale": false,
      "missed_polls": 0,
      "battery": 87,
      "signal_strength": -72
    }
  },
  "ambient": {"temperature": 283.15, "humidity": 40.0},
  "solver": {
    "last_cost": 2.34,
    "feasible_combinations": 48,
    "total_combinations": 64
  }
}
```

Any process can read status.json: a web UI, `spriggler status`, a monitoring script, a cron job that sends alerts. The daemon doesn't need to know or care what reads it.

### spriggler.log

System-generated structured log. JSON-lines format — one JSON object per line, machine-parseable, self-contained. Written to the state directory.

**Event types:**

| Event | Description |
|---|---|
| `daemon.start` | Daemon startup with config summary, state dir, sensor/device counts |
| `daemon.stop` | Clean shutdown with total cycle count |
| `config.reload` | Config reload success or failure |
| `cycle.start` | Beginning of a control cycle |
| `sensor.reading` | Successful sensor read (temperature, humidity, battery, RSSI in SI) |
| `sensor.missed` | Sensor returned no data |
| `safety.alert` | Safety monitor alert with level and message |
| `safety.safe_mode` | Environment(s) entered safe mode |
| `safety.command` | Safety override forced a device state |
| `solver.result` | Solver evaluation: feasible/total combos, cost, chosen device states |
| `device.command` | Device state change (old → new) |
| `device.power` | Device power consumption in watts |
| `override.detected` | Manual override detected with hold time |
| `override.expired` | Manual override timer expired |
| `override.mismatch` | Device state mismatch corrected (no hold time) |
| `environment.summary` | Environment readings vs targets |

**Example log line:**

```json
{"ts": "2026-02-24T02:43:18.543Z", "event": "sensor.reading", "cycle": 2, "sensor_id": "flower_sensor", "environment": "flower", "temperature": 295.95, "humidity": 24.0, "battery": 90.0, "signal_strength": -86.0}
```

Every event includes `ts` (UTC ISO-8601) and `event` (type string). Cycle-scoped events include `cycle`. All temperature values in Kelvin. The console formatter translates these to human-readable output in the user's preferred unit.

The log file is append-only. Rotation and retention are future work (important for Pi deployments with limited storage).

## The Cost Function: How Spriggler Reasons

Spriggler doesn't use priority rankings between environments. It doesn't need them. Instead, it uses a continuous cost function that captures what a competent human operator intuitively knows: how bad is this situation, and how fast is it getting worse?

### The Shape of Cost

```
                          │
            cost           │         ╱
                          │        ╱
                          │       ╱
                          │      ╱
                          │    ╱
                          │  ╱
                          │╱         ___───
              ────────────┼───────── 
         absolute_min   min    ideal    max   absolute_max
```

- **Within ideal range:** Cost is zero. No action needed.
- **Between ideal and min/max targets:** Cost rises gently. The solver acts if it can, but won't sacrifice other environments for marginal improvement.
- **Between min/max targets and absolute limits:** Cost rises steeply. This is where resources get redirected from comfortable environments to struggling ones.
- **At absolute limits:** Cost is effectively infinite. The safety monitor has already intervened, but the solver also treats this as the highest priority.

This curve means the solver naturally does what a human would do:

- Veg chamber dropping toward absolute minimum while flower is 2°F above ideal? **Move heat from flower to veg.** The cost reduction in veg dwarfs the cost increase in flower.
- Humidifier failed in flower, humidity at 38% instead of 48% target? **Shrug.** Cost is low — well within the gentle slope of the curve. Don't steal resources from environments that need them more.
- Heater failed in flower but it's 80°F outside and flower is at 78°F? **Non-event.** Cost is near zero. Solver doesn't waste energy solving a problem that doesn't exist.

### Why Not Priority Rankings?

A priority system would say "veg is priority 1, flower is priority 2." But that's wrong — sometimes flower matters more than veg, depending on what's happening *right now*. A priority system can't express "the veg chamber is fine but the flower chamber is about to freeze." The cost function can, because it's driven by the actual state of each environment relative to its targets and limits.

Priority is static. Situational awareness is dynamic. Spriggler reasons dynamically.

### Multi-Environment Solving

The solver minimizes total cost across all environments simultaneously, subject to device and circuit constraints. This means it naturally discovers cross-environment strategies:

**Resource transfer instead of generation:**
- Veg needs heat, flower has excess heat
- Inter-chamber fan costs 23W
- Veg heater costs 1500W
- Solver chooses: fan (lower energy cost, reduces cost in veg, barely increases cost in flower)

**Graceful degradation on device failure:**
- Veg heater locked out by safety monitor
- Solver re-solves without it
- Finds: run flower heater harder + inter-fan moves warm air to veg
- Flower temperature drops slightly but stays within acceptable range
- Veg temperature stabilizes above critical threshold
- Total cost across both environments is minimized given available resources

**Triage under constraint:**
- Pod heater and veg heater both struggling in extreme cold
- Circuit can't run both at full capacity
- Solver evaluates: pod is closer to its absolute minimum, cost curve is steeper there
- Solver allocates more circuit capacity to pod heater
- This isn't a coded rule — it falls out of the cost function because the pod's situation is more dire
- The solver knows nothing about seedlings or plants — it only knows that the pod's cost curve is screaming louder than the veg chamber's

These behaviors emerge from the optimization. No one codes "if veg heater fails, use flower heater plus inter-fan." The solver discovers it because it's the lowest-cost combination of available device states.

## Electrical Constraints

Circuits have amperage limits. The solver treats electrical capacity as a constraint alongside thermodynamic targets.

If running Device A would exceed circuit capacity, the solver looks for alternatives - possibly running an equivalent device on a different circuit, or achieving the goal indirectly through environmental transfer.

## Failure Modes

### Device Failure - Silent

The most dangerous failure. Device stops working but doesn't report an error. Detected by the safety monitor through device-sensor coherence:

- Heater claims ON, temperature dropping → heater dead, lock it out, alert user
- Heater claims OFF, temperature spiking → heater stuck on, kill the circuit if possible, alert user immediately

**Response:** Lock out device, re-solve without it, alert user. Do not retry a device that has failed coherence checks without user intervention.

### Device Failure - Runaway

Device operates beyond expected parameters. Temperature climbing at a rate inconsistent with the device's calibrated transfer function.

**Response:** Force device to safe state. If temperature continues climbing (device ignoring commands), escalate — kill the smart plug, alert user with maximum urgency. This is a fire or total-loss scenario.

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

### Sensor Failure

When a sensor stops responding:

1. Mark it stale after N missed reports
2. Safety monitor cannot verify environment safety
3. Affected devices enter safe state
4. Alert user: "Sensor offline, environment in safe mode"

**No sensor data = no device operation.** The system will not blindly control devices without feedback.

**Auto-recovery:** The daemon does not shut down during a sensor stale event. It continues listening. When valid sensor data resumes, the safety monitor clears the stale flag, the solver re-engages, and normal operation resumes automatically. Transient BLE dropouts, Bluetooth stack hiccups, and brief interference resolve themselves without human intervention.

### Calibration Drift

When passive recalibration detects consistent prediction errors:

1. Diagnose: is the error in the envelope model or a device model?
2. If envelope: "Envelope conductance has changed. Possible physical cause. Inspect structure."
3. If device: "Device X thermal output has decreased 20% since calibration. Possible equipment degradation."
4. Widen prediction tolerance to avoid false safety alerts while drift is within manageable bounds
5. If drift exceeds manageable bounds, recommend physical inspection and potential recalibration

## Development Philosophy

### Test-Driven Development

Spriggler uses a strong TDD approach. Every component has a clearly defined interface and contract, and every contract is tested before implementation begins.

**Why TDD is critical for this project:**

- The architecture is designed for third-party driver development. Contributors writing drivers for new hardware need confidence that their code meets the contract and won't break the system.
- The safety monitor makes life-or-death decisions for what's in the controlled environments. Its behavior under every failure mode must be verified.
- The solver's correctness can be validated against known physics. If the solver says "run the heater for 10 minutes," we can compute analytically whether that's right.
- The system runs unattended on remote hardware. Bugs discovered in production mean dead plants, ruined fermentation, or worse. Catch them in tests.

### Driver Conformance Test Harness

Any driver — sensor or device — can be validated against the standard contract by running the conformance test suite. A contributor writes a driver, plugs it into the harness, and gets a pass/fail report.

**Sensor driver conformance:**

- Subclasses `SensorDriver` ABC
- `read()` returns a dict or None
- All keys are in the standard taxonomy (`temperature`, `humidity`, `battery`, `signal_strength`)
- All values are in SI units
- Temperature is in Kelvin, not Celsius, not Fahrenheit
- Humidity is in %RH
- Battery is in percent (0-100)
- `validate_config()` accepts valid config and raises on invalid
- `driver_name` property returns a string matching the registry name
- Driver handles hardware timeout without crashing (returns None)
- Driver handles garbage data without crashing (returns None, not garbage values)
- Driver does not block indefinitely

**Device driver conformance:**

- Subclasses `DeviceDriver` ABC
- `get_available_states()` returns a list of strings, always including `'off'` as the first element
- `set_state(state)` accepts any state from `get_available_states()`
- `get_current_state()` returns a string from `get_available_states()`
- `turn_on()` and `turn_off()` are convenience methods (default to first/last state)
- `is_on()` returns True if current state is not `'off'`
- `get_power()` returns watts or None if not supported
- `supports_countdown()` returns a boolean
- If `supports_countdown()` is True, `set_countdown(seconds, target_state)` accepts valid arguments
- `validate_config()` accepts valid config and raises on invalid
- `driver_name` property returns a string matching the registry name
- Driver handles network timeout gracefully
- Driver handles device-not-found gracefully

**Safety monitor test scenarios:**

- Temperature exceeds absolute max → devices enter safe state
- Temperature drops below absolute min → devices enter safe state
- Rate of change exceeds threshold → alert and investigation
- Device-sensor coherence failure → device lockout
- Sensor goes stale → affected devices enter safe state
- Battery warning threshold → alert
- Battery critical threshold → alert with urgency
- Multiple simultaneous failures → correct prioritization

**Solver test scenarios:**

- Single environment, single device → correct device state
- Single environment, constrained circuit → respects amperage limit
- Multi-environment, shared resources → cost-function-driven allocation
- Device failure, cross-environment recovery → discovers alternative paths
- All targets satisfied → minimum energy solution
- Known physics scenarios with analytical solutions → solver matches within tolerance

### Development Workflow

```
1. Define the interface and contract
2. Write the conformance/unit tests
3. Implement the component
4. Tests pass
5. Integration test against adjacent components
6. Review, commit
```

Each session starts with: "Here's architecture.md and README.md. We're working on spriggler 0.3. Today I want to work on X."

## What Carries Forward from 0.2

**Keep (with review):**

- Sensor drivers (Govee BLE — now uses govee-ble library, dual addressing for macOS)
- Device drivers (KASA, VeSync — to be ported to new DeviceDriver ABC)
- KASA hardware countdown timer management (proven, critical safety feature)
- BLE scanning via bleak (proven, reliable — now shared scanner architecture)
- Power state management
- Structured logging format (now JSON-lines in state directory)

**Discard:**

- Influence model
- Policy-based effects
- Per-property evaluation
- Sequential command issuing
- Hand-rolled Govee BLE advertisement parsing (use govee-ble library instead)
- Log and status files next to config (now in state directory)

## Decisions Made

- **Psychrometric library:** PsychroLib (existing package, not rolling our own)
- **Safety monitor:** Separate component, independent loop, veto authority over solver
- **Device trust model:** Sensors are truth, device self-report is advisory only
- **Multi-environment reasoning:** Cost function, not priority rankings. Solver minimizes total cost across all environments simultaneously. Resources flow to where the situation is most dire.
- **Calibration approach:** Active, device-driven. Each device calibration simultaneously characterizes the device's contribution and the envelope's conductance from the decay curve. No more 48-hour passive observation — a few hours of active experiments produces high-signal data across a wide property range.
- **Recalibration:** Passive and continuous during normal operation. Drift is treated as diagnostic information (something physical changed) rather than a calibration problem to silently correct.
- **Ambient sensor:** Required, not optional. The physics model cannot function without knowing boundary conditions.
- **Sensor driver contract:** Subclass `SensorDriver` ABC. `read()` returns dict with all reported values in SI units or None. Standard key taxonomy. One driver per physical sensor, no splitting by property. `driver_name` property for registry lookup.
- **Device driver contract:** Subclass `DeviceDriver` ABC. `get_available_states()` returns list of discrete states. `set_state()`/`get_current_state()` for control. `turn_on()`/`turn_off()`/`is_on()` as convenience methods. `get_power()` for energy monitoring. `supports_countdown()`/`set_countdown()` for hardware failsafes.
- **Driver-specific config:** Common fields (driver, environment, circuit, role) validated by daemon. Driver-specific fields in `driver_config` block validated by driver.
- **Package structure:** Sensors and devices in separate packages (`sensors/`, `devices/`), each with base ABC, implementations, and registry. Sensors are things you read, devices are things you command. Same hardware can have both a sensor driver and a device driver (e.g., KASA plug reads power, controls on/off).
- **Domain-agnostic solver:** The solver knows physics and cost functions. It knows nothing about plants, fish, fermentation, or any specific application. Domain knowledge lives in the config.
- **Govee BLE driver:** Uses govee-ble library for advertisement parsing. Shared BLE scanner (one scan for all sensors). Dual addressing mode: MAC address on Linux, BLE name suffix on macOS (CoreBluetooth doesn't expose MACs). H5151 gateway filtering. ObjC type casting for macOS compatibility.
- **Development approach:** Test-driven. Driver conformance harness for third-party driver contributors.
- **Solver implementation:** Brute-force enumeration over all feasible device state combinations. No scipy or external optimizer. Problem space is small enough (~4K-100K combinations for realistic setups) to evaluate exhaustively in under 50ms. Results are fully explainable: "tried N combinations, M within circuit limits, this one lowest cost." If future setups grow beyond feasible enumeration time, heuristics or branch pruning can be added without changing the interface.
- **Graduated device control:** All devices have discrete states. Binary devices are graduated devices with two states (`['off', 'on']`). No special casing. A VeSync humidifier reports `['off', 'low', 'mid', 'high']`. The solver enumerates all levels. Analog devices are discretized to meaningful steps; the physics model computes optimal continuous settings analytically in a second phase if needed.
- **Internal units:** All computation, calibration data, and stored logs use SI (Kelvin for temperature, %RH for humidity). User units appear only in the config file (converted to SI at load time) and display surfaces (logs to console, alerts, UI). Calibration data survives a user unit change because it was never stored in user units.
- **Executables:** Two entry points. `spriggler-daemon` runs the control loop. `spriggler <command>` is the CLI for everything else (display, calibrate, check, status, explain).
- **State directory:** All daemon output (`status.json`, `spriggler.log`, future `calibration/`) goes to `~/.spriggler/` by default. Override with `--state-dir` flag or `SPRIGGLER_STATE_DIR` env var. Both daemon and CLI use the same resolution logic. Config directory is read-only input, state directory is output.
- **Structured logging:** JSON-lines format to `spriggler.log` in the state directory. One self-contained event per line. Console output is a separate formatter that translates structured events to human-readable text. `--quiet` flag suppresses console; log file always gets everything.
- **Daemon health:** `running` flag in `status.json`. Set to `true` on startup, `false` on clean shutdown. Combined with timestamp staleness, enables three-state detection: running, stopped, crashed.
- **Process communication:** File-based only. No IPC, no sockets, no REST between daemon and CLI. Daemon reads config (checks mtime each cycle), reads calibration files, writes to state directory. CLI reads from state directory.
- **Power consumption tracking:** If hardware reports real watts (KASA plugs), record it during calibration and runtime. Feeds calibration accuracy, energy cost modeling, and degradation detection.
- **Manual override:** Per-device `manual_override_minutes` config field. Zero or missing means daemon corrects immediately (owns the device). Positive value means daemon respects a physical button press for N minutes, solver works around it, timer expires, daemon resumes. Safety monitor always has veto. No CLI or computer needed — just press the button on the device.

## Open Questions

- Interval-based scheduling: periodic timed pulses for irrigation, CO2 injection, sampling. Design settled conceptually, needs schema definition.
- Alert/notification mechanism: log-only, email, SMS, push?
- Log rotation and retention: critical for Pi deployments with limited SD card storage. Size-based or time-based rotation, configurable retention period.

---