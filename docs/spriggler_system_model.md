# Spriggler System Model v0.5

## 1. Problem Statement

Control measurable properties across one or more enclosed
environments, using actuators whose characteristics are
discovered through calibration.  The controller has no a priori
knowledge of device effects or environment dynamics — these
are measured.

The system is domain-agnostic.  A grow tent controls air
temperature and moisture.  A brewery controls fermentation
temperature.  An aquaculture system controls water temperature,
dissolved oxygen, and pH.  The controller operates on abstract
properties — numeric values with target ranges — not on
domain-specific concepts.

The system must meaningfully outperform local device controllers
(thermostats, humidistats, dosing controllers) by:

- Anticipating device inertia (coast/overshoot)
- Anticipating scheduled events (lights-on will add heat)
- Coordinating across properties (cooling will raise %RH —
  don't humidify if cooling is imminent)
- Adapting to changing external conditions
- Managing inter-environment coupling through shared paths

If it cannot do these things, it has no reason to exist.


## 2. Definitions

### 2.1 Medium

A **medium** is a physical substance that carries properties.
Examples: air, water, nutrient solution, soil, substrate.

Different media carry different properties.  Air carries thermal
energy and water vapor.  Water carries thermal energy, dissolved
gases, dissolved ions.  Soil carries moisture, nutrients, thermal
energy.

Connections between environments transport specific media.  A fan
moves air.  A pump moves water.  When medium moves, it carries ALL
properties of that medium simultaneously.

### 2.2 Properties

A **property** is a measurable scalar quantity of a specific
medium within an environment.  Properties come in two kinds:

**Fundamental properties** change only when something physically
adds, removes, or transports the underlying substance or energy.
Examples:
- Temperature (thermal energy per unit mass)
- Absolute humidity (grams of water per cubic meter of air)
- Dissolved oxygen concentration (mg/L)
- pH (hydrogen ion activity)
- CO₂ concentration (ppm)
- Electrical conductivity (mS/cm)
- Soil moisture (volumetric water content)

**Derived properties** are calculated from fundamental properties.
They change when their underlying fundamentals change, even if
nothing acted on them directly.  Examples:
- Relative humidity (%RH) — derived from absolute humidity and
  temperature.  Warming air reduces %RH with no change in
  moisture.
- DO saturation (%) — derived from dissolved oxygen and water
  temperature.  Warming water reduces %DO-sat with no change
  in oxygen.

The pattern: any property expressed as a percentage of a capacity,
where that capacity depends on another controlled property, is
derived.

**Why this matters:** An actuator that affects a fundamental
property will appear to affect derived properties that depend on
it.  A heater changes temperature (fundamental).  %RH changes as
a consequence (derived).  If the controller calibrates and acts on
the derived quantity, it sees phantom cross-effects — the heater
appears to be a dehumidifier.  Working in fundamental quantities
eliminates phantom cross-effects at the root.

This was the core failure of Spriggler v0.4.  The trajectory
planner calibrated the heater's effect on %RH (a phantom
cross-effect) and used it to select the heater as a dehumidifier
at 83°F.  Every subsequent fix (side-effect filtering, role-aware
scoring, energy penalties, property constraints) was a patch on
this fundamental error.  Working in absolute humidity would have
prevented the problem entirely.

**Handling:** The system includes a physics plugin library of
known property conversions.  Initial plugins:

- %RH ↔ absolute humidity (requires temperature)
- %DO-saturation ↔ DO concentration (requires water temperature)

The plugin architecture allows domain-specific conversions to be
added without modifying core controller code.  Each plugin
declares: the derived property name, the fundamental property it
maps to, the other fundamental properties required for conversion,
and the conversion function (derived→fundamental).

Sensor readings in derived units are converted to fundamental
units at the sensor boundary.  Targets expressed in derived units
are converted to fundamental units at startup (using a nominal
temperature or other reference value from config).  The controller
works exclusively in fundamental quantities internally.

If no plugin exists for a reported property, it is treated as
fundamental.  Users do not declare property relationships — the
physics plugins handle this.

### 2.3 Environments

An **environment** is a physical space containing one or more
media, each with measurable properties.

A grow tent is one environment containing:
- Air (temperature, absolute humidity, CO₂)
- Soil (moisture, EC, pH, temperature)
- Possibly a water reservoir (temperature, pH, EC)

The media within an environment interact through physics:
evaporation moves water from soil to air.  Thermal conduction
transfers heat between air and soil.  These inter-media
interactions within a single environment are physical couplings.
(See Future Research, Section 9.1.)

Each environment has:
- One or more media with their properties
- Sensors reporting property values of specific media
- Actuators that modify properties of specific media
- Passive exchange with ambient and other environments

### 2.4 Ambient

**Ambient** represents external conditions that are measured but
not controlled.  There may be multiple ambient sources for
different media:
- Room air (boundary for air medium)
- Tap water supply (boundary for water medium)
- CO₂ supply (boundary for CO₂ injection)
- Ground temperature (boundary for soil medium)

Each ambient source provides boundary values for the properties
of its medium.

### 2.5 Connections

A **connection** is an exchange path between two environments or
between an environment and an ambient source.

**Same-medium connections** transport a medium and all its
properties.  A fan between two air spaces exchanges temperature,
moisture, CO₂ — everything the air carries.  A pump between two
water volumes exchanges temperature, pH, DO, ions.

**Cross-medium connections** provide limited exchange, typically
thermal only.  A tank wall transfers heat between water inside
and air outside, but doesn't transfer dissolved chemicals to
the air.  A heat exchanger transfers thermal energy between two
air spaces without mixing the air itself (or mixes, depending
on design — this is declared in config).

All connections have:
- Two endpoints (environments or ambient sources)
- The medium(s) being exchanged
- Passive conductance (always present — leakage, diffusion)
- Optional active transfer devices that increase conductance

Conductance is **symmetric and sign-agnostic**.  A fan doesn't
cool or heat.  It increases the rate of equilibration.  The
direction of flow is determined by the differential between
endpoints.  When the differential reverses, the flow reverses.

### 2.6 Actuators

An **actuator** modifies properties of a specific medium within
a specific environment.  Two categories:

**Energy devices** inject or remove a quantity:
- Each has one or more intended fundamental properties
  (declared by role in config)
- Most devices affect one property: heater → temperature,
  humidifier → absolute moisture, dosing pump → concentration,
  irrigation valve → soil moisture
- Some devices affect multiple properties inherent to their
  physics: an evaporative cooler removes heat (temperature ↓)
  AND adds moisture (absolute humidity ↑) — both are intended
  effects, not side effects, because that's how evaporative
  cooling works
- For each intended property, calibration discovers the rate
  and coast profile independently
- May also have a thermal byproduct (see below)

The controller treats single-effect and multi-effect devices
identically.  A single-effect device is simply the common case
where the list of intended properties has one entry.

**Transfer devices** move medium between environments:
- Fans, pumps, valves, ducts
- Increase conductance between endpoints for all properties
  of the transported medium
- Conductance delta discovered by calibration
- A Peltier cooler is modeled as a transfer device: it moves
  thermal energy from one environment (or medium) to another,
  with a declared sink (typically ambient)

**Graduated devices** have multiple non-off states (low, medium,
high).  Each state has independently calibrated rates and coast
profiles per intended property.  The controller selects the
appropriate state based on distance from target and coast
overshoot:

```
For a device with states [off, slow, fast]:
    Each state has its own rate and coast_overshoot per
    intended property.
    
    If far from target:
        Use the fastest state whose coast won't overshoot
        past the target boundary on ANY intended property.
    
    If close to target:
        Use the slowest state, for precision.
    
    The switching point between states is where the faster
    state's coast would overshoot target:
        switch_to_slow = target_boundary - coast_overshoot_fast
    
    Below switch_to_slow: use fast state
    Above switch_to_slow: use slow state (or off)
```

For multi-effect devices, the coast constraint is evaluated
per intended property.  The most conservative constraint wins —
the device downshifts or turns off when ANY intended property's
coast would overshoot its target boundary.

**Thermal byproduct:** Powered devices convert some electricity
to heat.  For a heater this is the intended effect (temperature
is in its intended property list).  For a 600W grow light, a
large fraction becomes heat (a significant byproduct — temperature
is NOT in its intended property list, but the thermal effect is
real and must be accounted for).  For a 2W fan, the thermal
byproduct is negligible.

The thermal byproduct is quantified by one of:
- Calibrated temperature rate (always available — ground truth
  for the specific installation)
- Measured power draw (if power sensing is available — useful
  for real-time variation, e.g., dimmer on lights)
- Declared wattage in config (optional fallback)

Power sensing is not required.

### 2.7 Sensors

A **sensor** reports the state of a specific medium within a
specific environment.  It has:
- A **sample timestamp** (wall-clock time of physical measurement)
- A **delivery interval** (expected time between reports,
  declared in config — e.g., 30s for Govee BLE)
- Resolution and noise characteristics
- The medium and properties it measures
- Optional: declared accuracy/precision for fusion weighting

The controller never assumes a reading is fresh.


## 3. Dynamic Model

### 3.1 State Equation

For environment *i*, medium *m*, fundamental property *p*:

```
dx_imp/dt = Σ_j  g_ijm(t) · (x_jmp - x_imp)   [exchange]
          + Σ_k  u_k(t) · r_kp                  [actuators]
          + d_imp(t)                             [disturbance]
```

Where:

- **x_imp** is the value of fundamental property p of medium m
  in environment i
- **g_ijm(t)** is the conductance for medium m between
  environments i and j (zero if they don't share the medium or
  have no connection)
- **u_k(t)** is the activation level of actuator k
- **r_kp** is the calibrated rate of actuator k on property p
  (nonzero for intended properties, plus thermal byproduct)
- **d_imp(t)** is unmodeled disturbance

For energy devices: r_kp is nonzero for the device's intended
properties and for thermal byproduct on temperature (if the
device has significant power dissipation and temperature is not
already an intended property).

For transfer devices: the effect is captured entirely by g_ijm,
not by r_kp.  The transfer device has a conductance delta that
applies to all properties of the medium.

### 3.2 Conductance

```
g_ijm(t) = g_passive_ijm + Σ g_active_ijm · u_transfer(t)
```

Conductance is **symmetric and sign-agnostic**.  The direction
of flow is (x_jmp - x_imp).

For same-medium connections, conductance is equal across all
properties of that medium.

For cross-medium connections (heat exchangers, tank walls),
conductance exists only for thermal energy.

### 3.3 Actuator Dynamics

**Coast/overshoot:** After shutoff at t₀:
```
coast_effect_kp(t) = interpolate(coast_profile_kp, t - t₀)
```

Empirical data from calibration.  Each state of a graduated
device has its own coast profile per intended property.

**Startup delay:** Some devices have significant startup time
(industrial gas heaters with pre-start cycles, compressors
with anti-short-cycle timers).  Startup characteristics are
declared in config as an optional onset delay.  The controller
accounts for this delay in anticipatory control and in actuator
verification (the verification window cannot be shorter than
the declared onset delay).


## 4. Calibration

### 4.1 User-Declared (Config)

- Environment list
- Media present in each environment
- Connection topology: which environments connect, via what
  medium, through what device (if active)
- Actuator intended properties and directions (one or more per
  device)
- Actuator-environment-medium assignments
- Actuator states available (off, on, low, high, etc.)
- Actuator onset delay (optional — for slow-starting devices)
- Scheduled devices and their schedules
- Target ranges per environment per medium per property
- Safety limits
- Sensor assignments (which sensor, which environment,
  which medium, which properties)
- Sensor delivery interval (expected time between reports)
- Optional: declared wattage for devices without power sensing
- Optional: declared sensor accuracy for fusion weighting

### 4.2 Calibration-Discovered

Per energy device, per state, per intended property:
- **r_kp**: rate of change (units/second)
- **coast_profile_kp**: post-shutoff trajectory (time series)

Per energy device with significant thermal byproduct:
- **thermal_byproduct_rate**: temperature rate from power
  dissipation (only for devices where temperature is not
  already an intended property)

Per transfer device:
- **g_active_m**: conductance delta for the transported medium

Per environment, per medium, per property:
- **g_passive**: passive conductance to ambient
- **τ**: time constant (= 1/g_passive)

### 4.3 Calibration Process

For each actuator:

1. **Pre-condition** environment to suitable starting point.
2. **Activate** and measure rate of change on all intended
   fundamental properties simultaneously.  Gate on fresh
   sensor arrivals.
3. **Deactivate** and measure coast profile on all intended
   properties.
4. **Measure decay** to characterize passive conductance.

For graduated devices, repeat steps 1-4 for each non-off state.

For devices with thermal byproduct: the temperature rate is
measured during step 2 alongside the intended properties.  If
the device's intended properties do not include temperature,
the measured temperature rate is recorded as thermal byproduct.

### 4.4 What Calibration Cannot Discover

- Device intended properties (user declares)
- Connection topology and media types (user declares)
- Physical property conversions (physics plugin library)
- Disturbance patterns
- Actuator startup/onset characteristics (user declares)


## 5. Controller Architecture

### 5.1 Design Principles

1. **Property-agnostic.**  No domain-specific terms in control
   logic.

2. **Works in fundamental quantities.**  Derived properties
   converted at sensor boundary via physics plugin library.

3. **Anticipatory.**  Schedule-aware.  Predicts consequences
   of imminent changes using calibrated rates and physics
   conversions.

4. **Differential-aware.**  Transfer devices evaluated on
   differential between endpoints.

5. **Role-aware.**  Each actuator evaluated on its intended
   properties only.

6. **Inertia-compensating.**  Turn-off points account for coast
   on all intended properties.

7. **Stale-data-aware.**  Actions qualified by data age.

8. **Globally coordinated.**  Multi-environment decisions
   consider the full system state, not just per-connection
   pairs.

### 5.2 Energy Devices

Hysteresis with coast compensation, evaluated independently per
intended property, per graduated state:

```
For actuator k with states [off, slow, fast],
    in environment i,
    with intended fundamental properties [p1, p2, ...],
    each with direction d:

Each state s has, for each intended property p:
    rate_sp:   calibrated rate
    coast_sp:  coast overshoot

For each intended property p with direction d:
    If d == increase:
        For each state s:
            turn_off_sp = target_max_p - coast_sp
            turn_on_sp  = turn_off_sp - hysteresis_sp
    If d == decrease:
        For each state s:
            turn_off_sp = target_min_p + |coast_sp|
            turn_on_sp  = turn_off_sp + hysteresis_sp

State selection:
    Evaluate each intended property independently.
    If evaluations agree: use agreed state.
    If evaluations conflict (one says ON, another OFF):
        The property furthest outside its target range wins.
    
    Coast constraint: the device cannot be in a state where
    ANY intended property's coast would overshoot its target.
    The most conservative constraint across all intended
    properties determines the maximum allowable state.
```

Hysteresis derived from calibrated rate — one cycle's change,
with minimum floor for noise rejection.

### 5.3 Transfer Devices

Decision based on differential between connected environments:

```
For transfer device connecting environments i and j
    via medium m:

For each fundamental property p of medium m:
    differential_p = x_jmp - x_imp
    need_i = direction environment i needs p to move
    need_j = direction environment j needs p to move

    helps_i = (need_i == increase and differential_p > 0) or
              (need_i == decrease and differential_p < 0)
    hurts_j = transfer would push j outside its target for p

If helps_i and not hurts_j:   favor ON
If hurts_j:                    favor OFF
If |differential_p| < min_useful: favor OFF (ineffective)
If need_i == none:             favor OFF (not needed)
```

When a transfer carries multiple properties and the evaluation
conflicts (helps temperature, hurts moisture), the property
furthest outside its target range takes priority.

min_useful_differential derived from calibrated conductance
delta — below a certain differential, the transfer rate is
too slow to matter.

### 5.4 Global Coordination

When multiple environments are connected, transfer decisions
must consider the full system state.  Independent per-connection
evaluation can oscillate when environments form chains or loops.

Example: Environment A (tropical fish, 80°F) connected to B
(cool species, 68°F) connected to C (outdoor pond, 55°F).

Independent evaluation might run the A-B transfer to cool A
while simultaneously running the B-C transfer to warm B from C.
But if C is cold, the B-C transfer cools B, undermining the
A-B transfer's goal.

**Global coordination approach:**

1. Evaluate all environments' needs simultaneously — which
   environments are above target, which below, which in range.

2. Identify beneficial transfer paths: sequences of connections
   where the source has excess and the sink has deficit for the
   same property.

3. Evaluate paths end-to-end: does activating this path of
   transfers move the system toward global compliance without
   pushing any intermediate environment outside its target?

4. Activate transfers that improve total system compliance.
   Deactivate transfers that create conflicts.

The coordination runs once per cycle, considering all
environments and connections together.

### 5.5 Anticipatory Control

The controller knows the schedule and the calibrated rates.
It predicts one step ahead.

**Schedule anticipation:**
```
If a scheduled device will change state within
    lookahead_window:
    
    Estimate the effect using calibrated rates and/or
    thermal_byproduct_rate
    
    If the change will push a property outside target:
        pre-start a compensating device
    If the change will naturally correct a property currently
    outside target:
        suppress the device currently correcting it
```

The lookahead window is computed from calibration data, not
an arbitrary constant:

```
lookahead = needed_compensation / compensating_device_rate
```

Where needed_compensation is the predicted property change from
the scheduled event (e.g., lights add 0.2°F/min × expected
overshoot duration), and compensating_device_rate is the
calibrated rate of the device that will counteract it (e.g.,
exhaust fan cools at 0.85°F/min).  The lookahead is the time
the compensating device needs to establish its effect before the
scheduled event hits.

**Derived property anticipation:**
```
When about to change a fundamental property (e.g., cool air):
    Use physics plugin to predict effect on derived properties
    (e.g., cooling → %RH rises)
    
    If predicted derived value will reach target naturally:
        don't activate the device that corrects that derived
        property (don't humidify — %RH will rise on its own)
    If predicted derived value will exceed target:
        pre-activate compensation
```

This is one prediction step.  "What will conditions be after
this action?"  Not trajectory optimization.

### 5.6 Conflict Resolution

1. **Same property, multiple devices, same direction:** Prefer
   the device whose rate best matches the deviation from target.
   Far from target: use the fastest device whose coast won't
   overshoot.  Close to target: use the slowest device for
   precision.  Same graduated-state logic applied across
   devices: the switching point between devices is where the
   faster device's coast would overshoot target.

2. **Multi-effect device, properties conflict:** Property
   furthest outside target takes priority.

3. **Different devices, different properties, same environment:**
   Independent.  Each device acts on its intended properties.

4. **Transfer multi-property conflict:** Property furthest
   outside target takes priority.

5. **Circuit limits:** Shed lowest-impact devices first.


## 6. Sensor Model

### 6.1 Freshness Classification

Every sensor reading is classified by its age relative to the
sensor's declared delivery interval.  The delivery interval is
declared in config (e.g., 30s for Govee BLE, 5s for a wired
thermocouple).  All freshness thresholds are derived from it:

- **Fresh** (age < 1.5 × delivery_interval): full confidence.
  Normal control decisions.
- **Aging** (age < 3 × delivery_interval): reduced confidence.
  Suppress aggressive actions — don't start high-power devices
  or make state changes based on aging data.
- **Stale** (age < 10 × delivery_interval): low confidence.
  Hold current device states.  Don't make new decisions.  Log.
- **Dead** (age ≥ 10 × delivery_interval): sensor has failed.
  Enter safe mode for the environment.  Turn off energy-adding
  devices.  Log alert.

The multipliers (1.5, 3, 10) are configurable defaults.  They
represent: one missed delivery (fresh→aging), several missed
deliveries (aging→stale), and sustained absence (stale→dead).
These can be tuned per sensor if needed, but the defaults should
be reasonable for most sensor types.

### 6.2 Multiple Sensors Per Property

When multiple sensors report the same fundamental property of
the same medium in the same environment, the system fuses their
readings using established best practices:

**Outlier rejection:** Median filtering removes readings that
are statistically inconsistent with other sensors reporting the
same property.  A reading more than 3σ from the median is
flagged as a potential sensor fault, logged, and excluded from
fusion.

**Fusion:** Kalman filtering produces an optimal state estimate
from multiple noisy, asynchronous sensor inputs.  The Kalman
filter naturally handles:
- Sensors with different update rates (BLE every 30s vs wired
  every 5s)
- Sensors with different noise characteristics (weighted by
  declared or estimated accuracy)
- Asynchronous arrivals (each sensor updates the state estimate
  independently when a new reading arrives)

The Kalman filter state is the current best estimate of the
fundamental property value and its uncertainty.  The uncertainty
informs the controller's confidence — high uncertainty
suppresses aggressive actions, similar to stale data handling.

For single-sensor environments, the Kalman filter degenerates
to simple low-pass filtering with the sensor's noise variance
as the measurement noise parameter.

### 6.3 Actuator Verification

After commanding an actuator, the controller expects sensor
confirmation on its intended fundamental properties.

The verification window is derived from the device's
characteristics, not a fixed constant:

```
verification_window = max(
    onset_delay,                  # declared in config (0 for
                                  #   relay devices, 90s for an
                                  #   industrial gas heater)
    min_detectable_change / rate, # time for the calibrated rate
                                  #   to produce a sensor-
                                  #   detectable change
    delivery_interval × 2         # need at least 2 fresh sensor
                                  #   readings to detect a trend
)
```

Verification logic:
```
After commanding actuator k to state s at time t₀:
    Wait for verification_window to elapse.
    Monitor intended properties for movement in expected
    direction.
    
    If confirmed on all intended properties: verified, clear.
    
    If not confirmed after verification_window:
        Retry command.
        Reset baseline to current value.
        Wait another verification_window.
        Never give up while controller still wants this state.
        Retry interval: max(verification_window, 5 minutes)
        to avoid command flooding.
```

Verification checks intended fundamental properties only.
Does not verify derived property changes (those are physics,
not device response).  Does not verify thermal byproduct
(temperature changes from byproduct may be too slow to detect
within the verification window).


## 7. Calibration Drift Detection

Calibrated parameters (rates, coast profiles, conductance)
change over time as devices age, environments are modified, or
operating conditions shift.  The system must detect when
calibration no longer matches reality and flag for recalibration.

### 7.1 Approach: EWMA Prediction Error Monitoring

The established method for detecting gradual drift in process
control is the Exponentially Weighted Moving Average (EWMA)
control chart.  EWMA tracks a running weighted average of
prediction error, giving more weight to recent observations
while smoothing noise.  This prevents recalibration on a single
miss while catching consistent systematic error.

For each actuator action, the controller compares predicted
vs actual outcome on each intended property:

```
prediction_error = actual_change - predicted_change
ewma_error = λ · prediction_error + (1 - λ) · ewma_error_prev
```

Where λ (typically 0.1-0.3) controls sensitivity.  Lower λ
smooths more aggressively, requiring more consistent error
before triggering.

### 7.2 Drift Detection Threshold

A drift alarm is raised when the EWMA exceeds a threshold,
typically 3σ of the baseline prediction error (established
during initial calibration):

```
if |ewma_error| > 3 · σ_baseline:
    flag device for recalibration
    log drift magnitude and direction
```

### 7.3 Design Considerations

- **Don't recalibrate on a single miss.**  Sensor noise,
  transient disturbances, and stale data all produce one-off
  prediction errors.  The EWMA smooths these out.

- **Detect both offset drift and proportional drift.**  An
  aging heater might produce less heat (proportional — rate
  decreases) or have different coast behavior (offset — coast
  profile shifts).  Monitor both rate prediction error and
  coast prediction error independently.

- **Separate sensor drift from actuator drift.**  If ALL
  devices show prediction error in the same direction, the
  sensor may have drifted, not the devices.

- **Recalibration is expensive.**  It takes the environment
  offline for characterization.  The drift detection threshold
  should be set high enough that recalibration is triggered
  only when the error meaningfully affects control quality,
  not on statistical significance alone.


## 8. Implementation Phases

### Phase 0: Command Line Interface and Calibration

CLI for configuration, calibration, and manual operation.
Calibration code updated to work in fundamental quantities
(absolute humidity instead of %RH).  Physics plugin for
%RH ↔ absolute humidity.  Sensor freshness tracking with
sample timestamps.

This is the foundation.  Everything else depends on it.

### Phase 1: Single Environment, Single Medium

Energy devices with graduated hysteresis + coast compensation.
Transfer devices with differential-based decisions.
Kalman filter for sensor fusion (supports single sensor as
degenerate case).
All control logic operates in fundamental quantities.

Test on seedling pod.  Success criteria:
- Heater coast doesn't overshoot past target
- Fan turns off when ambient exceeds pod temperature
- Humidifier suppressed when predicted cooling will raise %RH
  to target naturally
- Measurably outperforms local device controllers

### Phase 2: Schedule Anticipation

Pre-cooling before lights-on.  Derived property prediction
around temperature changes.  Lookahead window computed from
calibration data.

### Phase 3: Multi-Environment with Global Coordination

Inter-environment transfer.  Freezer duct as cold source.
Global coordination evaluates all environments and connections
together per cycle.

### Phase 4: Multi-Medium

Soil sensors and irrigation in grow environment.  Water
chemistry in aquaculture.  Additional physics plugins for
domain-specific derived properties.

### Phase 5: Advanced Control (if needed)

If threshold-based control proves insufficient for complex
multi-environment coordination, upgrade to model predictive
control using a real optimization library.  The calibrated
model and sensor infrastructure support this — only decision
logic changes.


## 9. Future Research

### 9.1 Inter-Media Coupling Within Environments

Evaporation from soil adds moisture to air.  Air temperature
affects soil temperature through conduction.  These are
physical couplings between media within a single environment.

Open questions:
- Are these adequately captured by calibrating device effects
  (irrigation's air humidity side effect)?
- Do they need explicit modeling as inter-media conductance?
- Can they be handled as physics plugins (evaporation is a
  calculable coupling between soil moisture and absolute
  humidity)?

### 9.2 Dynamic Target Surfaces (VPD and Similar)

Some derived quantities are the real control target rather than
a sensor conversion.  Vapor Pressure Deficit (VPD) is the
standard metric for plant transpiration management.  VPD depends
on both temperature and absolute humidity.

A VPD target (e.g., 0.8-1.2 kPa) cannot be converted to a
static absolute humidity range because the required humidity
changes with temperature.  At 80°F, a VPD of 1.0 kPa requires
one absolute humidity.  At 75°F, it requires a different one.

This makes VPD a **constraint surface** across two fundamental
quantities rather than a simple target range.  The controller
would need to recompute the humidity target every cycle based on
current temperature.

This requires a richer plugin interface — not just sensor
conversion, but dynamic target computation:

```
Given: current values of related fundamental properties
       desired range for the derived quantity
Return: current target range for the controlled fundamental
        property
```

Note that temperature and %RH together yield absolute humidity,
dew point, AND VPD through the same underlying physics.  A
single comprehensive air-moisture plugin could serve all three.

This is deferred to a later phase because it introduces
cycle-dependent targets, which adds complexity to the
controller's threshold logic.  Phase 1 works with static
targets on fundamental quantities.

### 9.3 Physics Plugin Library Extensions

Initial set:
- %RH ↔ absolute humidity (requires temperature)
- %DO-saturation ↔ DO concentration (requires water temperature)

Potential additions as domains expand:
- Dew point (derived from temperature and absolute humidity)
- VPD (see Section 9.2)
- Water activity — relevant in food processing

Each plugin is a Python module that registers its conversions.
Low up-front cost, high long-term value.
