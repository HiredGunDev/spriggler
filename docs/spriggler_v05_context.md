# Spriggler v0.5 — Context for New Conversation

## What Is Spriggler

Spriggler is an open-source Python environmental controller for
grow environments, aquaculture, brewing, and similar applications.
It calibrates device and environment characteristics automatically,
then uses physics-informed control to maintain target conditions.

The project is at ~/Projects/spriggler.  The system model document
is at ~/Projects/spriggler/docs/spriggler_system_model.md — read
it first, it's the design spec for everything we're building.

## What Exists

The repo is virgin except for docs/.  All v0.4 code is preserved
under git tag `v0.4` in the same repo.  We are starting clean,
importing useful code from v0.4 as needed.

## Hardware Available for Testing

**Seedling pod** (simplest test case):
- Govee H5075 BLE temperature/humidity sensor (reports ~every 30s,
  0.1°C resolution, reports %RH not absolute humidity)
- Second Govee H5075 for ambient
- 600W ceramic heater on KASA smart strip (plug "Heater")
- 42W LED grow light on KASA smart strip (plug "Lights")
- 4" inline exhaust fan on KASA smart strip (plug "Fan")
- VeSync Dual 200S humidifier (cloud-controlled, unreliable,
  states: off/low/high)
- KASA KP303 smart strip "seedling" provides power monitoring
  and relay control for heater, light, fan

**Veg/flower chambers** (future, larger):
- 600W HPS lights (massive thermal byproduct)
- Same sensor/device types, larger scale

**Potential additions:**
- Freezer-to-pod cold air duct (AC substitute)
- Soil moisture sensors
- Additional Govee sensors

## Key Lessons from v0.4 (Don't Repeat These)

### The Phantom Cross-Effect (Root Cause of All v0.4 Failures)

The v0.4 calibration measured the heater's effect on %RH and
found it "dried" the air at -0.73%/cycle.  This is a phantom
cross-effect — the heater doesn't remove moisture, it raises
temperature, which lowers RELATIVE humidity.  Absolute humidity
is unchanged.

Every solver variant chose the heater as a dehumidifier because
the %RH cross-effect made it look beneficial for humidity.  The
heater ran at 83°F because its humidity "benefit" outweighed its
temperature cost.  Four patches (side-effect filtering, role-aware
scoring, energy penalties, property constraints) all failed to
fully fix it because they were treating the symptom.

**The fix in v0.5:** Work in fundamental quantities internally.
Convert %RH → absolute humidity at the sensor boundary using the
Magnus formula.  The heater's calibrated effect on absolute
humidity is zero.  Problem eliminated at the root.

### The Fan-Always-On Problem

v0.4's threshold controller gave the exhaust fan a static
temperature threshold.  The fan ran 99.8% of the time, including
when ambient (82°F) exceeded pod temperature (76°F) — the fan
was importing heat.

**The fix in v0.5:** Transfer devices (fans, pumps) are NOT
evaluated on static thresholds.  They're evaluated on the
differential between connected environments.  Fan ON only when
the differential is favorable.  This falls naturally out of the
conductance model — the fan increases conductance, flow direction
depends on the sign of (ambient - pod).

### The Trajectory Planner Was Overengineered

We built a 35-step forward simulator with greedy rollout, cost
functions, discount factors, and energy penalties.  It produced
behavior nobody could predict or debug.  Each fix created a new
emergent pathology.

**The fix in v0.5:** No trajectory optimization.  Hysteresis
thresholds with coast compensation (computed from calibration),
differential-based transfer decisions, and one-step anticipatory
prediction for schedule events.  Simple, predictable, debuggable.

### BLE Sensors Are Unreliable

Govee BLE delivers readings sporadically.  Sometimes every 15s,
sometimes 90s gaps.  The v0.4 daemon made decisions on stale
data without knowing it.  The `_sample_time` tracking we added
(wall-clock time of BLE advertisement arrival) is essential.

**In v0.5:** Every sensor reading carries a sample timestamp.
Freshness classification (fresh/aging/stale/dead) derived from
the sensor's declared delivery interval.  Aggressive actions
suppressed on aging data.  Hold state on stale data.  Safe mode
on dead sensor.

### VeSync Cloud Control Is Unreliable

The VeSync humidifier's cloud API drops commands silently.  The
fire-and-forget pattern (send command, don't wait for
confirmation, verify via sensor feedback) works.  Patient retry
(5 min interval, never give up) handles persistent failures.
Never call get_current_state() on VeSync in the control loop —
it blocks and returns stale data.

### Calibration Data Is Good

The calibration system (characterize.py) produces solid data:
rates, coast profiles, envelope time constants.  The coast
detection (seen_change guard, primary-property focus, no time
caps) works well.  This code is worth carrying forward, updated
to work in fundamental quantities.

## What v0.5 Needs to Implement

Read the system model document for full details.  Summary:

### Phase 0: CLI and Calibration
- `spriggler <command>` CLI with subcommands (start, stop,
  status, calibrate, config)
- Calibration updated for fundamental quantities
- Physics plugin: %RH ↔ absolute humidity
- Sensor freshness tracking

### Phase 1: Single Environment Controller
- Energy devices: hysteresis + coast compensation, graduated
  state selection based on distance from target
- Transfer devices: differential-based decisions
- Kalman filter for sensor fusion
- Actuator verification with patient retry
- Must outperform local device controllers

### Phase 2+: Schedule anticipation, multi-environment, etc.

## Code Worth Salvaging from v0.4 (git tag v0.4)

These are under src/spriggler/ in the v0.4 tag:

- **sensors/govee.py** — BLE scanner, _sample_time tracking.
  Well-tested, works.  Import as-is or near as-is.
- **devices/kasa.py** — KASA discovery and connection manager.
  Solid.
- **devices/vesync.py** — Rate limiter wrapper.  Keep.
- **devices/vesync_device.py** — Fire-and-forget command
  pattern, _ensure_device startup.  Keep.
- **struct_log.py** — Structured JSON logger.  Clean, useful.
- **config/loader.py** — JSON config loading.  May need
  significant revision for new config schema.
- **calibrate/characterize.py** — Coast detection, rate
  estimation, decay measurement.  Good bones, needs revision
  for fundamental quantities.
- **calibrate/power.py** — Power draw measurement.  Works.
- **safety/monitor.py** — Safety limits, lockouts.  Keep
  concept, may rewrite.

Do NOT carry forward:
- solver/planner.py (trajectory planner — the thing that failed)
- solver/threshold.py (too simple, static thresholds)
- solver/solver.py (old snapshot solver)
- solver/cost.py (cost function — not used in v0.5)
- The daemon's control loop (rewrite from scratch)

## Developer

Captain (William Maness/William Aubry Kelley), retired software
engineer, Colonial Beach, Virginia.  Pro se litigant in Hemlock
Lane LLC v. Ravenwolf Marine.  Decades of software experience
from IMSAI 8080 through Python.  Favors simplicity, physical
realism, and correctness over cleverness.

## Engineering Principles (Learned the Hard Way)

- No arbitrary constants.  Every number comes from calibration
  or declared config.
- No cost functions.  They create emergent behavior that can't
  be predicted or debugged.
- Work in fundamental physical quantities.  Derived quantities
  at boundaries only.
- Every device has intended effects (declared) and may have
  side effects (physics).  The controller acts on intended
  effects only.
- Transfer devices are conductance modifiers, not heaters/coolers.
  The differential determines the direction.
- Waterfall the design, then implement.  Don't Agile into a
  pile of patches.
- If the code can't explain what it's doing in plain English,
  the design is wrong.