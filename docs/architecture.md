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

## System Components

```
┌─────────────────────────────────────────────────────┐
│                     CLI / Web UI                     │
├─────────────────────────────────────────────────────┤
│            config/    calibration/    logs/          │
├─────────────────────────────────────────────────────┤
│                   SPRIGGLER DAEMON                   │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │              SAFETY MONITOR                     │  │
│  │  Independent loop. Reads sensors. Vetoes.       │  │
│  │  Cannot be overridden by solver.                │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Sensors  │ │ Devices  │ │ Physics  │ │ Solver │  │
│  │ Drivers  │ │ Drivers  │ │  Model   │ │        │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘  │
└─────────────────────────────────────────────────────┘
         │              │
         ▼              ▼
   [BLE, WiFi]    [KASA, VeSync, etc.]
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

#### Phase 1: Envelope Characterization (48+ hours, environment empty, all devices off)

The most important thing calibration learns is the **effective R-value** of each environment's envelope. This is a physical constant of the structure — it doesn't change with the seasons, only with physical modifications (adding insulation, breaking a seal, leaving a vent open).

Heat transfer through an envelope follows:

```
Q = (1/R) × A × ΔT

Where:
  Q  = heat flow (watts)
  R  = thermal resistance of the envelope
  A  = effective surface area
  ΔT = temperature differential (inside - ambient)
```

We don't need to know R and A independently. We solve for the aggregate `(1/R) × A` as a single coefficient — call it the **envelope conductance**. This one number captures the total thermal leakiness of the space: walls, ceiling, floor, seals, air gaps, everything.

**How it's measured:**

1. Environment is empty. All devices off.
2. Ambient sensor records outside temperature continuously.
3. Interior sensor records inside temperature continuously.
4. System runs for at least 48 hours — two full day/night cycles.
5. As interior temperature drifts toward ambient, the system records the rate of change at each moment alongside the current differential.
6. From many (rate, differential) data points across varying conditions, the system fits the envelope conductance coefficient.
7. Two daily cycles provide a range of differentials to verify the coefficient is consistent. If it's consistent across a 15-degree ambient swing, it will hold across a 50-degree seasonal swing.

**Verification:** If the derived coefficient is NOT consistent across different differentials, the system flags this — it suggests nonlinear behavior like wind-dependent air leaks, solar gain on one side of the structure, or other effects that a simple R-value model doesn't capture. The system reports this to the user and may need a more complex envelope model for that environment.

**Moisture characterization** follows the same principle but is more complex. Moisture transfer involves both diffusion through materials and air exchange (infiltration). The system derives an aggregate moisture conductance coefficient the same way — watch humidity drift toward ambient humidity with everything off, fit the coefficient from (rate, differential) pairs.

#### Phase 2: Device Characterization (automated, sequential)

With the envelope characterized, device calibration measures each device's **energy contribution** — isolated from envelope effects.

1. For each device:
   a. Record current ambient and interior conditions
   b. Predict envelope drift using the Phase 1 model
   c. Turn on device
   d. Safety monitor watches for runaway conditions
   e. Record sensor changes over time
   f. Subtract predicted envelope drift to isolate device contribution
   g. Record power consumption (if available)
   h. Compute device energy contribution rate (watts thermal, grams moisture/hr, etc.)
   i. Turn off device, wait for settling
2. For cross-environment devices (inter-chamber fans):
   a. Record both source and destination environment states
   b. Activate device
   c. Record changes in both environments
   d. Compute transfer rates for heat and humidity
3. For devices with secondary effects (lights generating heat):
   a. Measure primary effect (illumination schedule)
   b. Measure thermal contribution as a side effect
   c. Record both — solver needs to know that turning lights on adds heat

**Device contributions are approximately constant** — a 1500W heater outputs ~1500W regardless of outdoor temperature. What changes is the net effect, which the solver computes at runtime by combining the device contribution with the current envelope loss rate.

#### Phase 3: Validation

1. Predict: "Turning on device X for 10 minutes at current conditions should change temperature by Y degrees"
2. Execute and compare
3. Report model accuracy
4. If accuracy is poor, diagnose: is the envelope model off, or the device model?
5. Store validated model in calibration/

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

### config/

User-authored. Describes what exists:

- **Environments** - Physical spaces, their connections (air source/sink)
- **Sensors** - What hardware, where it reports, which environment
- **Devices** - What hardware, how to control it, which environment, which circuit
- **Circuits** - Electrical capacity limits
- **Schedules** - Time-based targets for each environment
- **Safety** - Absolute limits per environment, per-device safe states, rate-of-change thresholds, coherence windows

Config does NOT describe what devices do to properties. That's learned.

### calibration/

System-generated during calibration. Contains:

- **Envelope models** - Derived envelope conductance (aggregate thermal and moisture leakiness) per environment, with confidence metrics and the ambient temperature range observed during calibration
- **Envelope linearity assessment** - Whether the conductance coefficient was consistent across observed differentials, or whether nonlinear effects were detected
- **Device energy contributions** - Measured energy output of each device (watts thermal, grams moisture/hr), isolated from envelope effects
- **Device secondary effects** - Measured side effects (e.g., thermal output of grow lights)
- **Cross-environment transfer rates** - Heat and moisture transfer rates for inter-chamber devices, measured in both source and destination environments
- **Device response signatures** - Expected sensor response timing and magnitude for safety monitor coherence checking
- **Power profiles** - Actual wattage under load per device
- **Validation results** - Prediction accuracy from Phase 3 spot checks
- **Calibration metadata** - When calibrated, ambient temperature range during calibration, duration, model accuracy scores

**Passive recalibration data** is stored separately from initial calibration:

- **Prediction accuracy log** - Rolling accuracy score for envelope and device models
- **Drift tracking** - Trend data for envelope conductance and device contributions over time
- **Diagnostic events** - Timestamped records of detected coefficient changes with possible causes

Calibration is environment-specific. Initial calibration requires an empty environment. Passive recalibration runs continuously during normal operation and provides diagnostic information rather than silent correction.

### logs/

System-generated during operation:

- **Decisions** - What the solver chose and why
- **Safety events** - Vetoes, lockouts, limit breaches, coherence failures
- **Predictions vs actuals** - Model validation
- **Anomalies** - When reality diverges from model
- **Events** - Device commands, sensor readings, errors

Structured format for machine parsing. Human-readable for debugging.

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

- `read()` returns a dict
- All keys are in the standard taxonomy
- All values are in SI units
- Temperature is in Kelvin, not Celsius, not Fahrenheit
- Humidity is in %RH
- Battery is in percent (0-100)
- Driver handles hardware timeout without crashing
- Driver handles garbage data without crashing (returns error, not garbage values)
- Driver does not block indefinitely

**Device driver conformance:**

- `turn_on()` and `turn_off()` accept no arguments and return success/failure
- `is_on()` returns a boolean
- `get_power()` returns watts or None if not supported
- `supports_countdown()` returns a boolean
- If `supports_countdown()` is True, `set_countdown(seconds, target_state)` accepts valid arguments
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

- Sensor drivers (Govee BLE — replace hand-rolled parsing with govee-ble library)
- Device drivers (KASA, VeSync)
- KASA hardware countdown timer management (proven, critical safety feature)
- BLE scanning via bleak (proven, reliable)
- Power state management
- Structured logging format

**Discard:**

- Influence model
- Policy-based effects
- Per-property evaluation
- Sequential command issuing
- Hand-rolled Govee BLE advertisement parsing (use govee-ble library instead)

## Decisions Made

- **Psychrometric library:** PsychroLib (existing package, not rolling our own)
- **Safety monitor:** Separate component, independent loop, veto authority over solver
- **Device trust model:** Sensors are truth, device self-report is advisory only
- **Multi-environment reasoning:** Cost function, not priority rankings. Solver minimizes total cost across all environments simultaneously. Resources flow to where the situation is most dire.
- **Envelope characterization:** Derive physical constants (effective R-value / envelope conductance) that hold across seasons, not snapshot coefficients that expire. 48-hour minimum initial calibration with two daily cycles.
- **Recalibration:** Passive and continuous during normal operation. Drift is treated as diagnostic information (something physical changed) rather than a calibration problem to silently correct.
- **Ambient sensor:** Required, not optional. The physics model cannot function without knowing boundary conditions.
- **Sensor driver contract:** Returns dict with all reported values in SI units. Standard key taxonomy. One driver per physical sensor, no splitting by property.
- **Driver-specific config:** Common fields (driver, environment, circuit, role) validated by daemon. Driver-specific fields in `driver_config` block validated by driver.
- **Domain-agnostic solver:** The solver knows physics and cost functions. It knows nothing about plants, fish, fermentation, or any specific application. Domain knowledge lives in the config.
- **Govee BLE parsing:** Use govee-ble library (pip install govee-ble) instead of hand-rolled parsing. Proven across thousands of installations, handles sub-zero temperatures correctly.
- **Development approach:** Test-driven. Driver conformance harness for third-party driver contributors.
- **Solver implementation:** Brute-force enumeration over all feasible device state combinations. No scipy or external optimizer. Problem space is small enough (~4K-100K combinations for realistic setups) to evaluate exhaustively in under 50ms. Results are fully explainable: "tried N combinations, M within circuit limits, this one lowest cost." If future setups grow beyond feasible enumeration time, heuristics or branch pruning can be added without changing the interface.
- **Graduated device control:** All devices have discrete states. Binary devices are graduated devices with two states (`['off', 'on']`). No special casing. A VeSync humidifier reports `['off', 'low', 'mid', 'high']`. The solver enumerates all levels. Analog devices are discretized to meaningful steps; the physics model computes optimal continuous settings analytically in a second phase if needed.
- **Internal units:** All computation, calibration data, and stored logs use SI (Kelvin for temperature, %RH for humidity). User units appear only in the config file (converted to SI at load time) and display surfaces (logs to console, alerts, UI). Calibration data survives a user unit change because it was never stored in user units.

## Open Questions

- Interval-based scheduling: periodic timed pulses for irrigation, CO2 injection, sampling. Design settled conceptually, needs schema definition.
- Web UI protocol: REST API, WebSocket, or pure file-based?
- Alert/notification mechanism: log-only, email, SMS, push?

---
