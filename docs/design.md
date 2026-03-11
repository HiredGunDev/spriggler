# Spriggler v0.4 — Trajectory-Based Control

## Status

Design document. Not yet implemented.

## Problem Statement

Spriggler v0.3 uses a snapshot optimizer: each 15-second cycle, the
solver evaluates every device combination, predicts the environment
state ONE cycle ahead, and picks the lowest-cost option. This produces
three classes of failure:

**Myopic heater control.** The solver turns the heater on at 83°F
because the predicted next-cycle state (83.5°F) is marginally
closer to ideal than the alternative (82.7°F cooling naturally).
It cannot see that the ceramic heater's thermal mass will coast
the temperature to 88°F over the next 5 minutes after shutoff.
A human looking at the same thermometer would never turn the
heater on above 80°F — they see the trajectory, not the instant.

**Missing coast dynamics.** The calibration stores coast as a single
overshoot number and duration. The heater's coast wasn't captured
at all (stale BLE readings during the 30-second coast detection
window masked the ongoing temperature rise). The solver literally
doesn't know the heater overshoots. Even where coast data exists,
a single number can't tell the solver what temperature will be at
30, 60, or 90 seconds after shutoff.

**Phantom humidity drift.** A default humidity envelope conductance
caused the solver to predict humidity drifting toward ambient at
0.76%/cycle — a phantom flux that doesn't exist in a sealed pod.
This distorted every humidity-related decision. Fixed in v0.3.2
but symptomatic of the broader problem: the model is too simple
to capture the real physics.

The result: a 600-watt heater cycling at 85-93°F in a pod targeted
for 72-80°F, while a threshold controller trivially holds the range.

## Design Principle

**Think in trajectories, not snapshots.**

A human controller watches the rate of temperature change, notices
whether the rate is accelerating or flattening, mentally simulates
forward ("if I turn the heater on now, it'll reach 78 in about 3
minutes, coast to maybe 80, then start dropping"), and acts on that
projection. They don't optimize a single number at a single instant.

Spriggler v0.4 replicates this by:

1. **Calibrating trajectories** — capturing rate curves, coast
   profiles, and decay curves as time series, not single numbers.
2. **Planning over a horizon** — simulating forward through multiple
   cycles and scoring the entire trajectory, not just the endpoint.
3. **Deriving the horizon from physics** — the planning window is
   set by the measured coast duration and envelope time constant,
   not hardcoded.

## Architecture Overview

```
Sensors ──→ Daemon Loop ──→ Trajectory Planner ──→ Device Commands
                ↑                    ↑
                │                    │
           Recent History     Calibration Data
           (ring buffer)      (rate curves,
                              coast profiles,
                              envelope τ)
```

The daemon loop still runs every 15 seconds. But instead of asking
"what's the best action RIGHT NOW?", it asks "what action sequence
over the next N minutes keeps the trajectory closest to target?"

### Components Changed

| Component | v0.3 | v0.4 |
|-----------|------|------|
| Calibration storage | Single rate per device per property, single coast overshoot number | Rate curves, coast profile (time series), decay curves |
| Calibration process | Fixed 5-min coast cap, convergence-based cutoffs | Physics-driven phase durations, coast runs until primary property reverses |
| Physics model | `predicted = current - envelope_loss + device_contribution` (one step) | Forward simulation over N steps using calibrated curves |
| Solver | Enumerate all combos, score one-step prediction | Enumerate strategies, simulate trajectories, score integral cost |
| Cost function | Instantaneous distance from target | Integral of distance over planning horizon |
| Daemon state | Stateless per cycle | Maintains recent sensor history (ring buffer) for slope estimation |

### Components Unchanged

- Device drivers (KASA, VeSync, mock)
- Sensor drivers (Govee, mock)
- Config system and loader
- Safety monitor (hard limits remain independent of solver)
- Schedule system
- Structured logging
- CLI

---

## Part 1: Richer Calibration

### What We Capture Now (v0.3)

Per device, per state, per property:
- `rate_per_second`: single linear rate (K/s or %RH/s)
- `std_error`: uncertainty on that rate

Per device (aggregated):
- `coast.{property}.overshoot`: single number (K or %RH)
- `coast.{property}.duration`: single number (seconds)

Per environment:
- `envelope.conductance.temperature`: single number (1/s)
- `envelope.time_constant.temperature`: single number (s)

### What We Need (v0.4)

Per device, per state, per property:
- `rate_per_second`: linear rate (unchanged)
- `std_error`: uncertainty (unchanged)

Per device (new or enhanced):
- `coast_profile.{property}`: array of `{elapsed_s, value}` samples
  recording the actual coast trajectory from device shutoff until
  the property reverses direction and begins returning toward
  baseline. This replaces the single overshoot/duration pair.
- `coast_peak.{property}`: the peak (or trough) value reached
  during coast, and the time to reach it. Derived from the profile.
- `coast_duration_s.{property}`: time from shutoff to peak.
  Derived from the profile.

Per environment (unchanged):
- Envelope conductance and time constant stay as they are.
  The envelope is a passive property of the enclosure and is
  well-captured by a single exponential.

### Calibration Process Changes

**Coast phase (Phase 2):**

Current behavior: coast runs until all properties settle (3
consecutive readings with no change in the coast direction)
or 5 minutes, whichever comes first. The 5-minute cap cuts off
the heater's coast before the peak is reached.

New behavior: coast runs until the PRIMARY property (determined
by device role via `ROLE_EFFECTS`) reverses direction. For a
heater, that's temperature — wait until temperature stops rising
and starts falling. For a humidifier, that's humidity. Other
properties are still recorded but don't gate the phase end.

There is no fixed time cap. The coast runs as long as the
physics requires. For a ceramic heater in a small pod, this
might be 5-8 minutes. For a large room with massive thermal
inertia, it could be 15-20 minutes. The calibration duration is
determined by the device and environment, not by an arbitrary
constant.

The coast phase records every sample with timestamp into the
`coast_profile` array. This is the actual measured trajectory
that the solver will use for forward simulation.

**Decay phase (Phase 3):**

Current behavior: decay runs until the envelope estimator
converges (RE < 5%) or 5 minutes. Short observation windows
produce poor fits for devices that create large differentials.

New behavior: decay runs until the envelope estimator converges
OR the differential drops below 20% of its starting value,
whichever comes first. The convergence threshold stays at
RE < 5%. The 20% differential floor ensures we don't waste time
measuring noise when the signal is exhausted. No fixed time cap.

**Pre-conditioning (unchanged in principle):**

The schedule-derived pre-conditioning logic from v0.3.2 is
retained. The environment is driven to operational starting
conditions before each characterization experiment. The
fan-first heater delay and temperature-only safety during
pre-conditioning remain.

**Primary property focus:**

Coast detection and phase termination use only the property the
device primarily affects (from `ROLE_EFFECTS`). Temperature
falling during humidifier coast is envelope decay, not humidifier
coast. Humidity slowly drifting during heater coast is not the
heater's coast. Each device's calibration focuses on what that
device actually does.

### Calibration File Format (v0.4)

```json
{
  "device_id": "seedling_heater",
  "environment": "seedling",
  "calibrated_at": "2026-03-08T...",
  "power_draw_watts": 536.2,
  "effects": {
    "on": {
      "seedling": {
        "temperature": {
          "direction": "increase",
          "rate_per_second": 0.0306,
          "std_error": 0.00076,
          "type": "energy"
        },
        "humidity": {
          "direction": "decrease",
          "rate_per_second": -0.0420,
          "std_error": 0.00406,
          "type": "energy"
        }
      }
    }
  },
  "coast_profile": {
    "on": {
      "temperature": [
        {"elapsed_s": 0, "value": 301.5},
        {"elapsed_s": 15, "value": 302.1},
        {"elapsed_s": 30, "value": 302.8},
        {"elapsed_s": 45, "value": 303.2},
        {"elapsed_s": 60, "value": 303.5},
        {"elapsed_s": 90, "value": 303.7},
        {"elapsed_s": 120, "value": 303.6},
        {"elapsed_s": 150, "value": 303.4}
      ],
      "humidity": [
        {"elapsed_s": 0, "value": 58.2},
        {"elapsed_s": 15, "value": 57.8},
        {"elapsed_s": 30, "value": 57.5}
      ]
    }
  },
  "coast_peak": {
    "on": {
      "temperature": {
        "peak_value": 303.7,
        "peak_elapsed_s": 90,
        "overshoot": 2.2
      }
    }
  },
  "envelope_conductance": 0.000576,
  "raw_data": { ... }
}
```

The `coast_profile` is the key new data. It's the actual measured
trajectory, sampled at the sensor interval. The solver interpolates
from this curve when simulating forward.

---

## Part 2: Trajectory Planner (Solver v0.4)

### Current Solver (v0.3)

```
for each device_combination:
    predicted = current - envelope_loss + Σ device_effects
    cost = distance_from_target(predicted)
pick combination with lowest cost
```

One step. No memory. No trajectory.

### New Solver (v0.4)

```
for each candidate_strategy:
    trajectory = simulate_forward(
        current_state,
        recent_history,   # for slope estimation
        strategy,         # device actions over horizon
        calibration,      # rates, coast profiles, envelope
        horizon_steps     # derived from coast duration + τ
    )
    cost = integral_cost(trajectory, targets)
pick strategy with lowest integral cost
execute first cycle's action
```

### Planning Horizon

The planning horizon must be long enough to capture the
consequences of the current action. The minimum horizon is:

```
horizon = max(coast_duration) + envelope_settling_fraction
```

Where `max(coast_duration)` is the longest coast among all active
devices (for the heater, ~5-8 minutes), and
`envelope_settling_fraction` is a fraction of τ sufficient to see
the trend (say τ/6, which for a 30-minute τ is 5 minutes).

For the seedling pod: horizon ≈ 8 + 5 = 13 minutes ≈ 52 cycles.

The horizon is computed from calibration data, not hardcoded.
Different environments with different devices will have different
horizons.

### Candidate Strategies

The v0.3 solver enumerates all device combinations (12 for the
current setup). Each combination is a single action applied for
one cycle.

The v0.4 solver enumerates STRATEGIES. A strategy is a device
combination applied for the FIRST cycle, with the assumption that
subsequent cycles will follow the solver's own logic. The simplest
approach:

For each candidate first-action (12 combinations):
1. Apply that action in the simulation
2. For subsequent simulated cycles, apply the greedy best action
   (lowest next-step cost) — this is the v0.3 solver running
   inside the simulation
3. Record the full trajectory
4. Score the trajectory

This is a one-step-lookahead with simulated rollout. It's not
full tree search (which would be 12^52 = intractable), but it
captures the key insight: "if I turn the heater on now, what
happens over the next 13 minutes?"

The heater-at-83°F failure is caught because the simulated
rollout shows the coast pushing to 88°F, which dominates the
trajectory cost even though the first-step cost was low.

### Forward Simulation

The simulator applies, at each step:

```
for each step in horizon:
    # Envelope decay toward ambient
    for prop in properties:
        predicted[prop] -= conductance[prop] * (predicted[prop] - ambient[prop])

    # Device contributions
    for device in active_devices:
        for prop in affected_properties:
            predicted[prop] += rate[device][state][prop] * dt

    # Coast contributions (for devices that were recently turned off)
    for device in recently_off_devices:
        elapsed = time_since_shutoff(device)
        coast_delta = interpolate(coast_profile[device][prop], elapsed)
        predicted[prop] = apply_coast(predicted[prop], coast_delta)
```

The coast interpolation is the critical new piece. When the heater
turns off at step N, the simulator looks up the coast profile and
applies the measured trajectory offsets for each subsequent step.
At step N+4 (60 seconds later), the profile says temperature is
+2.0K above the shutoff value. At step N+8, it's +2.2K (the peak).
At step N+12, it's +1.8K (decaying). This is the actual measured
curve, not an approximation.

### Trajectory Cost

The v0.3 cost function scores a single point:

```
cost = f(predicted_temp, target) + f(predicted_humidity, target)
```

The v0.4 cost function scores a trajectory:

```
total_cost = 0
for step in trajectory:
    step_cost = f(step.temp, target) + f(step.humidity, target)
    total_cost += step_cost * discount_factor^step_index
```

The discount factor (e.g., 0.98 per cycle) ensures near-term
deviations matter more than far-future predictions, which are
increasingly uncertain. The integral captures the AREA of deviation,
not just the endpoint. A trajectory that briefly spikes to 88°F
costs more than one that gently approaches 80°F, even if both end
at the same place.

### Sensor History and Slope

The daemon maintains a ring buffer of recent sensor readings
(last ~5 minutes). This provides:

- **Current slope** (dT/dt, dH/dt): linear regression over the
  last N readings. The solver uses this to detect "temperature is
  falling at 0.5°F/min" versus "temperature is stable."

- **Slope validation**: if the predicted trajectory diverges
  significantly from the observed slope, the model may be wrong.
  This could trigger a recalibration flag (future enhancement).

The slope is NOT used as a control signal (that would be PID).
It's used as initial conditions for the forward simulation —
the simulation starts from (current_value, current_slope), not
just current_value.

---

## Part 3: VeSync Reliability

The VeSync cloud dependency is a hardware/protocol problem, not a
solver problem. The solver redesign does not fix:

- Rate limiting from VeSync cloud API
- Silent command failures
- Latency between command and execution

### Confirmed Command Pattern

The core fix: `set_state()` doesn't return until the device
confirms. The driver owns the confirmation loop, not the caller.

```
set_state('high'):
    send command to cloud
    loop:
        query device state (rate-limited at 3-5s intervals)
        if state matches: return True
        if 30 seconds elapsed: return False (MISMATCH)
```

From the daemon's perspective, `set_state('high')` either
succeeds (device confirmed within 30s) or fails (timeout).
No separate MISMATCH handler needed — the driver handles
the retry internally.

The 3-second rate limiter already in the VeSync connection
manager spaces the polls naturally: ~6 checks over 30 seconds.
No new timing constants.

Benefits:
- Calibration: `set_state('high')` blocks until humidifier is
  confirmed running, then measurement begins. No guessing.
- Daemon: no MISMATCH correction loop hammering the API every
  cycle. The command either worked or it didn't.
- No dependency on power sensors for state verification.
  Works whether the humidifier is on a KASA plug or plugged
  directly into the wall.

If `set_state` returns False, the caller decides what to do:
- Calibration: retry once, then abort with clear error message
- Daemon: log the failure, skip the device for this cycle,
  continue with other devices. Don't retry until next cycle.

### Rate Limiting

The VeSync connection manager enforces a minimum interval
between any API call (~3 seconds). This caps total requests
at ~20/minute, well under VeSync's ~30/minute limit.

The confirmation loop's polling is the primary consumer of
API calls. At 3-second intervals over a 30-second window,
that's ~10 calls per set_state. Since the daemon only changes
humidifier state when the solver recommends a change (not every
cycle), the total API load is manageable.

### Local Control (Future)

The Levoit air purifier line (Core 200S/300S/400S) has been
reverse-engineered for local MQTT control via DNS redirect.
The Dual 200S humidifier (LUH-D301S) uses similar hardware
(ESP32 WiFi module) but nobody has documented local control
for the humidifier specifically. This remains a potential
future path to eliminate the cloud dependency entirely.

---

## Implementation Order

### Phase 1: Calibration Enhancement
- Modify coast phase to run until primary property reverses
- Remove fixed time caps from coast and decay
- Store coast profile as time series in calibration file
- Derive coast peak and duration from profile
- Test: run `calibrate device --device seedling_heater`, verify
  coast profile captures the full 5-8 minute overshoot arc

### Phase 2: Daemon History Buffer
- Add ring buffer of recent sensor readings to daemon
- Compute rolling slope (linear regression over last N samples)
- Log slope alongside current values for debugging
- Test: verify slope tracks real temperature changes

### Phase 3: Trajectory Planner
- Implement forward simulator using calibrated rates, coast
  profiles, and envelope conductance
- Implement trajectory cost (discounted integral)
- Implement strategy enumeration with simulated rollout
- Derive planning horizon from calibration data
- Replace current solver with trajectory planner
- Test: verify planner refuses to turn heater on above target max
  because the simulated trajectory shows coast overshoot

### Phase 4: Integration Testing
- Run full `calibrate all` with enhanced calibration
- Run daemon for 24 hours
- Analyze: does temperature stay in 72-80°F range?
- Analyze: does humidity stay in 60-80% range?
- Analyze: does the heater cycle cleanly without overshoot?
- Compare: does v0.4 outperform the v0.2 threshold controller?

---

## Success Criteria

1. Temperature stays within the target range (72-80°F) for >95%
   of cycles during a 24-hour run.
2. The heater never turns on when temperature is above target max.
3. The humidifier responds to commands reliably (power confirms
   state within 2 cycles).
4. The solver's cost decreases monotonically toward zero as
   conditions approach ideal, with no oscillation or hunting.
5. A human reading the log can understand why the solver made
   each decision.

## Open Questions

- **Coast profile resolution**: should coast samples be stored at
  the raw sensor interval (15s) or interpolated to a finer grid?
  Raw seems sufficient — the sensor can't report faster anyway.

- **Multi-device coast interaction**: when the heater and humidifier
  turn off at the same time, are their coasts additive? Probably
  yes for independent properties (heater coast on temp + humidifier
  coast on humidity), but interaction effects (heater coast warming
  air which affects humidity) may matter. Start with additive,
  validate empirically.

- **Strategy search depth**: the one-step-lookahead with greedy
  rollout may not find globally optimal strategies. Is this good
  enough? For 4 devices with 12 combinations, likely yes — the
  rollout captures the coast consequences, which is the main
  failure mode. Full tree search is intractable. Monte Carlo tree
  search is possible but probably overkill for 4 devices.

- **Recalibration triggers**: should the daemon detect when the
  model's predictions diverge from reality and flag a need for
  recalibration? This is valuable but not essential for v0.4.
  The slope validation in the history buffer provides the raw
  signal; acting on it is a future enhancement.

- **Humidity envelope**: v0.3.2 removed the default humidity
  envelope. Should humidity have ANY envelope conductance? In a
  truly sealed pod, no. But the fan vent, zip seams, and fabric
  walls do pass some moisture. This should be measured during
  calibration if possible — run the humidifier, shut everything
  off, and measure how fast humidity decays passively. If it's
  negligible over the planning horizon, leave it at zero.