"""General device characterization experiment.

Turns a device on, observes what changes across all measured properties
in all connected environments, and records the effect signature.

Design principles:
    1. SAFETY FIRST: Every sample checked against config limits.
       Device immediately shut off if any limit breached.
    2. ESTIMATOR-DRIVEN: Running curve fits with error bars determine
       when phases end. No blind timers running to max.
    3. HONEST RESULTS: "Cannot characterize" is a valid outcome.
       Weak signals are bounded, not fabricated.

Three possible outcomes per property:
    - Characterized: error bars tight enough for solver to use
    - Bounded: effect below measurement threshold (upper bound reported)
    - Failed: not enough data or conditions unsuitable

Usage:
    spriggler calibrate device --device seedling_fan
    spriggler calibrate device --device seedling_heater
"""

import json
import math
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from spriggler.calibrate.estimators import (
    RateEstimator,
    DecayEstimator,
    EstimateStatus,
)
from spriggler.calibrate.precondition import (
    compute_starting_targets,
    compute_schedule_envelope,
    check_reachability,
    precondition,
    ROLE_EFFECTS,
)
from spriggler.calibrate.thermal_fit import (
    ThermalSample,
    fit_decay,
    _linear_least_squares,
)
from spriggler.home import resolve_config, check_daemon, ConfigNotFoundError
from spriggler.units import from_kelvin


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class MultiEnvSample:
    """Synchronized readings from all environments at one moment."""
    timestamp: float
    environments: dict[str, dict[str, float]]


@dataclass
class ObservedEffect:
    """A detected effect of a device on one property in one environment."""
    property_name: str
    environment: str
    direction: str
    rate_per_second: float
    std_error: float
    effect_type: str  # 'energy', 'transfer', 'bounded', 'none'
    transfer_target: str | None = None
    conductance_delta: float | None = None
    conductance_delta_se: float | None = None


class PhaseInterrupt(Exception):
    pass

class AbortExperiment(Exception):
    pass

# Device role classifications for safety and envelope logic
HEATING_ROLES = {'heater', 'humidifier', 'light'}
COOLING_ROLES = {'cooler', 'dehumidifier', 'exhaust', 'intake',
                 'circulation', 'fan', 'vent'}


# ── Main entry point ─────────────────────────────────────────────────

def run_device_characterization(home: Path, args) -> None:
    """Run device characterization experiment."""

    # ── Check daemon ─────────────────────────────────────────────────
    if not args.force:
        status = check_daemon(home)
        if status.running:
            print("ERROR: Daemon running. Stop it or use --force.",
                  file=sys.stderr)
            sys.exit(1)

    # ── Load config ──────────────────────────────────────────────────
    try:
        config_path = resolve_config(home)
    except ConfigNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    from spriggler.config.loader import load_config
    try:
        config = load_config(str(config_path))
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    display_unit = config.get('_original_unit', 'F')
    device_id = args.device

    if device_id is None or device_id not in config['devices']:
        print(f"Error: device '{device_id}' not found.", file=sys.stderr)
        sys.exit(1)

    dev_cfg = config['devices'][device_id]
    dev_env = dev_cfg['environment']

    # ── Connected environments ───────────────────────────────────────
    connected_envs = {dev_env}
    env_cfg = config['environments'][dev_env]
    for c in env_cfg.get('connections', {}):
        connected_envs.add(c)
    for eid, ecfg in config['environments'].items():
        for c in ecfg.get('connections', {}):
            if c == dev_env or eid == dev_env:
                connected_envs.add(eid)
                connected_envs.add(c)

    # ── Sensors ──────────────────────────────────────────────────────
    env_sensors = {}
    for sid, scfg in config['sensors'].items():
        se = scfg['environment']
        if se in connected_envs:
            env_sensors.setdefault(se, {})[sid] = scfg

    # ── Safety limits ────────────────────────────────────────────────
    safety_limits = _extract_safety_limits(config)

    # ── Drivers ──────────────────────────────────────────────────────
    from spriggler.devices.registry import get_device_driver
    from spriggler.sensors.registry import get_sensor_driver

    dev_driver = get_device_driver(dev_cfg['driver'])(dev_cfg['driver_config'])

    sensor_drivers = {}
    for eid, sensors in env_sensors.items():
        for sid, scfg in sensors.items():
            drv = get_sensor_driver(scfg['driver'])(scfg['driver_config'])
            sensor_drivers[sid] = (drv, eid, scfg['properties'])

    # ── Calibration data ─────────────────────────────────────────────
    power_watts = _load_power_cal(home, device_id)
    envelope_cal = _load_envelope_cal(home, dev_env)
    # (Pre-conditioning replaces the old helper device mechanism)

    # ── Formatters ───────────────────────────────────────────────────
    def fmt_t(k):
        return f"{from_kelvin(k, display_unit):.1f}°{display_unit}"

    def fmt_d(dk):
        mult = 9/5 if display_unit == 'F' else (1 if display_unit == 'C' else 1)
        return f"{dk * mult:.1f}°{display_unit}"

    def read_all():
        now = time.time()
        envs = {}
        for sid, (drv, eid, _) in sensor_drivers.items():
            r = drv.read()
            if r:
                envs.setdefault(eid, {}).update(r)
        return MultiEnvSample(timestamp=now, environments=envs)

    def check_safety(sample, driver, did, check_max=True, check_min=True):
        """Check sample against safety limits.

        During calibration, we only check the limit the device could
        violate: max for heaters (which push temp up), min for
        coolers/fans (which push temp down). Both checked during
        helper and decay phases.
        """
        for eid, props in sample.environments.items():
            for prop, val in props.items():
                lims = safety_limits.get(eid, {}).get(prop, {})
                mx = lims.get('absolute_max')
                mn = lims.get('absolute_min')
                if check_max and mx is not None and val > mx:
                    driver.set_state('off')
                    v = fmt_t(val) if prop == 'temperature' else f"{val:.1f}"
                    l = fmt_t(mx) if prop == 'temperature' else f"{mx:.1f}"
                    print(f"\n  *** SAFETY: {eid}.{prop}={v} > {l} *** {did} OFF.")
                    return False
                if check_min and mn is not None and val < mn:
                    driver.set_state('off')
                    v = fmt_t(val) if prop == 'temperature' else f"{val:.1f}"
                    l = fmt_t(mn) if prop == 'temperature' else f"{mn:.1f}"
                    print(f"\n  *** SAFETY: {eid}.{prop}={v} < {l} *** {did} OFF.")
                    return False
        return True

    # ── Interrupt handler ────────────────────────────────────────────
    interrupt_count = 0
    original_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(signum, frame):
        nonlocal interrupt_count
        interrupt_count += 1
        if interrupt_count >= 2:
            raise AbortExperiment()
        raise PhaseInterrupt()

    # ── Print plan ───────────────────────────────────────────────────
    print(f"Device characterization: {device_id} ({dev_cfg.get('role', '?')})")
    print(f"  Environment: {dev_env}")
    print(f"  Connected: {', '.join(sorted(connected_envs))}")
    print(f"  Sensors: {', '.join(sorted(sensor_drivers.keys()))}")
    if power_watts:
        print(f"  Power: {power_watts:.1f}W")
    if envelope_cal:
        tau = envelope_cal.get('time_constant', {}).get('temperature')
        if tau:
            print(f"  Envelope τ: {tau:.0f}s ({tau/60:.1f} min)")
    _print_safety_limits(safety_limits, fmt_t, display_unit)
    print(f"  Sample interval: {args.sample_interval}s")
    print(f"  Max active: {args.max_active_minutes} min (hard limit)")
    print(f"  Max decay: {args.max_decay_minutes} min (hard limit)")
    print(f"  Phases end when estimators converge or declare no signal.")
    print()
    print("Ctrl-C once = end current phase. Twice = abort.")

    # ══════════════════════════════════════════════════════════════════
    # Phase 0: Baseline
    # ══════════════════════════════════════════════════════════════════
    print("\n── Phase 0: Baseline ──")
    dev_driver.set_state('off')
    time.sleep(2)

    baseline = _read_baseline_retry(read_all, connected_envs)
    if baseline is None:
        print("ERROR: No sensor readings after 30s.", file=sys.stderr)
        sys.exit(1)

    for eid in sorted(baseline.environments):
        p = baseline.environments[eid]
        parts = []
        if 'temperature' in p:
            parts.append(f"T={fmt_t(p['temperature'])}")
        if 'humidity' in p:
            parts.append(f"H={p['humidity']:.1f}%")
        print(f"  {eid}: {', '.join(parts)}")

    # ── Compute pre-conditioning targets from schedule ───────────────
    device_role = dev_cfg.get('role', '')
    schedule_envelope = compute_schedule_envelope(config, dev_env)
    starting_targets = compute_starting_targets(device_role, schedule_envelope)

    if starting_targets:
        print(f"\n  Starting condition targets (from schedule):")
        for prop, val in starting_targets.items():
            if prop == 'temperature':
                print(f"    {prop}: {fmt_t(val)}")
            else:
                print(f"    {prop}: {val:.1f}%")

    # ── Check if pre-conditioning is needed ────────────────────────
    # If current conditions already satisfy (or are beyond) the
    # starting targets, skip pre-conditioning entirely.
    available_devices = [
        (did, dcfg_i.get('role', ''))
        for did, dcfg_i in config['devices'].items()
        if dcfg_i['environment'] == dev_env and did != device_id
    ]
    current_env = baseline.environments.get(dev_env, {})
    current_amb = baseline.environments.get('ambient', {})

    needs_preconditioning = False
    if starting_targets:
        for prop, target in starting_targets.items():
            current = current_env.get(prop)
            if current is None:
                # No reading — can't verify conditions. Assume we
                # need pre-conditioning (safe default).
                needs_preconditioning = True
                continue
            effect = ROLE_EFFECTS.get(device_role)
            if effect and effect[0] == prop:
                _, direction = effect

                # Check if there's enough headroom between current
                # and the safety limit in the direction the device
                # pushes.  We need roughly 15 units (K or %RH) of
                # room for the estimator to converge.
                # If headroom is sufficient, skip pre-conditioning
                # even if we're above the schedule min.
                MIN_HEADROOM = 10.0  # K or %RH

                limit = safety_limits.get(dev_env, {}).get(
                    prop, {})

                if direction == 'increase':
                    safety_max = limit.get('absolute_max')
                    if safety_max is not None:
                        headroom = safety_max - current
                    else:
                        headroom = float('inf')
                    # Need pre-conditioning if not enough headroom
                    # OR if we're way above target (need to cool)
                    if headroom < MIN_HEADROOM:
                        needs_preconditioning = True
                    elif current > target + 2.0:
                        # Above target but have headroom — check if
                        # we can even cool down. If ambient is above
                        # target too, just calibrate from where we
                        # are; the headroom is what matters.
                        amb_val = current_amb.get(prop)
                        if amb_val is not None and amb_val < target:
                            needs_preconditioning = True
                        # else: can't cool, but headroom is fine,
                        # just calibrate from current position

                elif direction == 'decrease':
                    safety_min = limit.get('absolute_min')
                    if safety_min is not None:
                        headroom = current - safety_min
                    else:
                        headroom = float('inf')
                    if headroom < MIN_HEADROOM:
                        needs_preconditioning = True
                    elif current < target - 2.0:
                        amb_val = current_amb.get(prop)
                        if amb_val is not None and amb_val > target:
                            needs_preconditioning = True

    if needs_preconditioning:
        reachable, reason = check_reachability(
            starting_targets, current_env, current_amb,
            safety_limits, dev_env, available_devices)

        if not reachable:
            print(f"\n  *** Cannot establish starting conditions: "
                  f"{reason}")
            print(f"  *** Skipping {device_id}.")
            return

    # ── Pre-condition the environment ────────────────────────────────
    if needs_preconditioning:
        precondition(
            targets=starting_targets,
            current_readings=current_env,
            ambient=current_amb,
            env_id=dev_env,
            config=config,
            safety_limits=safety_limits,
            read_all=read_all,
            check_safety=check_safety,
            fmt_t=fmt_t,
            fmt_d=fmt_d,
            display_unit=display_unit,
            device_id_being_calibrated=device_id,
            sample_interval=args.sample_interval,
        )
        baseline = read_all()
        print(f"  Baseline after pre-conditioning:")
        for eid in sorted(baseline.environments):
            p = baseline.environments[eid]
            parts = []
            if 'temperature' in p:
                parts.append(f"T={fmt_t(p['temperature'])}")
            if 'humidity' in p:
                parts.append(f"H={p['humidity']:.1f}%")
            print(f"    {eid}: {', '.join(parts)}")
    else:
        if starting_targets:
            print(f"\n  Current conditions already near starting targets."
                  f" Skipping pre-conditioning.")

    # ══════════════════════════════════════════════════════════════════
    # Graduated device support: characterize each non-off state
    # ══════════════════════════════════════════════════════════════════
    states_to_cal = dev_driver.get_available_states()[1:]  # all non-off
    is_graduated = len(states_to_cal) > 1

    if is_graduated:
        print(f"\n  Graduated device: characterizing {len(states_to_cal)} "
              f"states: {states_to_cal}")

    # Accumulate per-state results
    all_state_results = {}  # {state: {effects, coast_data, raw_data}}
    last_decay_samples = []

    for state_idx, active_state in enumerate(states_to_cal):

        # ── Pre-condition between graduated states ───────────────────
        if state_idx > 0 and is_graduated:
            print(f"\n── Re-conditioning for next state ──")
            dev_driver.set_state('off')

            precondition(
                targets=starting_targets,
                current_readings=read_all().environments.get(dev_env, {}),
                ambient=read_all().environments.get('ambient', {}),
                env_id=dev_env,
                config=config,
                safety_limits=safety_limits,
                read_all=read_all,
                check_safety=check_safety,
                fmt_t=fmt_t,
                fmt_d=fmt_d,
                display_unit=display_unit,
                device_id_being_calibrated=device_id,
                sample_interval=args.sample_interval,
            )
            baseline = read_all()

        # ══════════════════════════════════════════════════════════════
        # Phase 1: Device ON — estimator-driven
        # ══════════════════════════════════════════════════════════════
        state_label = f"{device_id} → {active_state}" if is_graduated else device_id
        print(f"\n── Phase 1: Device ON ({state_label}) ──")

        signal.signal(signal.SIGINT, handle_interrupt)
        interrupt_count = 0

        if device_role in HEATING_ROLES:
            active_check_max, active_check_min = True, False
            print(f"  Safety: checking max only (role={device_role})")
        elif device_role in COOLING_ROLES:
            active_check_max, active_check_min = False, True
            print(f"  Safety: checking min only (role={device_role})")
        else:
            active_check_max, active_check_min = True, True
            print(f"  Safety: checking both limits (role={device_role})")

        IGNORE = {'battery', 'signal_strength'}
        rate_ests = {}    # {prop: RateEstimator}
        decay_ests = {}   # {prop: DecayEstimator}
        active_samples = []

        dev_driver.set_state(active_state)
        active_start = time.time()
        max_active_s = args.max_active_minutes * 60
        safety_stopped = False
        est_converged = False

        try:
            while True:
                elapsed = time.time() - active_start
                if elapsed > max_active_s:
                    print(f"\n  Max active time ({args.max_active_minutes} min).")
                    break

                sample = read_all()
                active_samples.append(sample)

                if not check_safety(sample, dev_driver, device_id,
                                    check_max=active_check_max,
                                    check_min=active_check_min):
                    safety_stopped = True
                    break

                # Feed estimators
                dp = sample.environments.get(dev_env, {})
                ap = sample.environments.get('ambient', {})

                for prop, val in dp.items():
                    if prop in IGNORE:
                        continue
                    if prop not in rate_ests:
                        rate_ests[prop] = RateEstimator(
                            convergence_threshold=0.10,
                            min_samples=10,
                            min_time_seconds=90.0,
                            no_signal_time_seconds=300.0,
                        )
                    rate_ests[prop].add(sample.timestamp, val)

                    if prop in ap:
                        diff = val - ap[prop]
                        if prop not in decay_ests:
                            decay_ests[prop] = DecayEstimator(
                                convergence_threshold=0.15,
                                min_r_squared=0.90,
                                min_samples=10,
                                min_time_seconds=90.0,
                                no_signal_time_seconds=300.0,
                            )
                        decay_ests[prop].add(sample.timestamp, diff)

                # Print
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                parts = []
                for eid in sorted(sample.environments):
                    ep = sample.environments[eid]
                    if 'temperature' in ep:
                        if eid != 'ambient' and 'temperature' in ap:
                            d = ep['temperature'] - ap['temperature']
                            parts.append(f"{eid}={fmt_t(ep['temperature'])} Δ{fmt_d(d)}")
                        else:
                            lbl = 'amb' if eid == 'ambient' else eid
                            parts.append(f"{lbl}={fmt_t(ep['temperature'])}")
                    if 'humidity' in ep and eid != 'ambient':
                        parts.append(f"H={ep['humidity']:.1f}%")

                print(f"  [{mins:02d}:{secs:02d}] ON  {'  '.join(parts)}")

                # Check all estimators
                all_done = True
                for p, e in rate_ests.items():
                    r = e._evaluate()
                    if r.status == EstimateStatus.RUNNING:
                        all_done = False
                        break

                if all_done and rate_ests and elapsed > 60:
                    print(f"\n  All estimators converged ({elapsed:.0f}s).")
                    est_converged = True
                    break

                time.sleep(args.sample_interval)

        except PhaseInterrupt:
            print(f"\n  Active phase ended by user ({len(active_samples)} samples).")
            interrupt_count = 0
        except AbortExperiment:
            print("\n  ABORT.")
            dev_driver.set_state('off')
            signal.signal(signal.SIGINT, original_handler)
            sys.exit(1)

        # ══════════════════════════════════════════════════════════════════
        # Phase 2: Coast — device off, primary property still moving
        # ══════════════════════════════════════════════════════════════════
        #
        # The coast phase captures the full trajectory of the device's
        # thermal/humidity inertia after shutoff.  There is no fixed time
        # cap — the phase runs until the PRIMARY property (determined by
        # device role) reverses direction.
        #
        # Temperature falling during humidifier coast is envelope decay,
        # not humidifier coast.  We don't let it gate the phase.
        #
        # The coast profile (time series of all properties) is stored
        # for the trajectory planner.

        print(f"\n── Phase 2: Device OFF (coast) ──")
        dev_driver.set_state('off')
        interrupt_count = 0

        coast_samples = []
        coast_start = time.time()

        # Determine primary property and coast direction from device role
        primary_prop = None
        primary_dir = None
        role_effect = ROLE_EFFECTS.get(device_role)
        if role_effect:
            primary_prop, effect_dir = role_effect
            # Coast continues in the direction the device was pushing
            if effect_dir == 'increase':
                primary_dir = 'rising'
            else:
                primary_dir = 'falling'

        # Fallback: if no role effect, use the strongest active-phase rate
        if primary_prop is None and rate_ests:
            best_prop = max(rate_ests.keys(),
                            key=lambda p: abs(rate_ests[p]._evaluate().value))
            primary_prop = best_prop
            r = rate_ests[best_prop]._evaluate()
            primary_dir = 'rising' if r.value > 0 else 'falling'

        if primary_prop:
            print(f"  Tracking: {primary_prop} ({primary_dir})")
        else:
            print(f"  No primary property to track.")

        # Also track all properties for coast profile recording
        coast_dirs = {}
        for prop, est in rate_ests.items():
            r = est._evaluate()
            if r.value > 0:
                coast_dirs[prop] = 'rising'
            elif r.value < 0:
                coast_dirs[prop] = 'falling'

        # Track extremes per property
        extreme_vals = {}
        extreme_times = {}
        stable_counts = {}
        STABLE_NEEDED = 3

        # Track whether we've seen the primary property actually
        # CHANGE value during coast.  If the sensor hasn't reported
        # a new reading yet (BLE reports every ~30s), identical
        # consecutive readings are stale cache, not stability.
        seen_change = {}  # {prop: bool}
        prev_coast_vals = {}  # {prop: last_value}

        # Minimum coast time: the sensor must have time to report
        # at least a few fresh readings.  At 15s sample interval
        # with ~30s BLE report period, we need at least 60s before
        # stability counts can be trusted.
        min_coast_s = 60

        # Record shutoff values for overshoot calculation
        shutoff_vals = {}
        last_active = active_samples[-1] if active_samples else None
        if last_active:
            for prop, val in last_active.environments.get(
                    dev_env, {}).items():
                if prop not in IGNORE:
                    shutoff_vals[prop] = val

        # Coast profile: {prop: [{elapsed_s, value}]}
        coast_profile = {}

        # Safety: absolute max time to prevent infinite coast if
        # sensor is stuck.  This is NOT a physics cap — it's a
        # sanity limit.  20 minutes should capture any reasonable
        # thermal mass.
        max_coast_sanity = 1200  # 20 min

        try:
            while True:
                elapsed = time.time() - coast_start
                if elapsed > max_coast_sanity:
                    print(f"\n  Coast sanity limit ({max_coast_sanity // 60}"
                          f" min). Primary may not have reversed.")
                    break

                sample = read_all()
                coast_samples.append(sample)

                dev_props = sample.environments.get(dev_env, {})
                amb_t = sample.environments.get(
                    'ambient', {}).get('temperature')

                # Record coast profile for ALL properties
                for prop, val in dev_props.items():
                    if prop in IGNORE:
                        continue
                    if prop not in coast_profile:
                        coast_profile[prop] = []
                    coast_profile[prop].append({
                        'elapsed_s': round(elapsed, 1),
                        'value': round(val, 4),
                    })

                # Update extreme tracking for all properties
                for prop, val in dev_props.items():
                    if prop in IGNORE or prop not in coast_dirs:
                        continue
                    if prop not in extreme_vals:
                        extreme_vals[prop] = val
                        extreme_times[prop] = elapsed
                        stable_counts[prop] = 0
                        seen_change[prop] = False
                        prev_coast_vals[prop] = val
                        continue

                    # Detect whether the sensor has actually
                    # reported a new value (not just stale cache)
                    if val != prev_coast_vals.get(prop):
                        seen_change[prop] = True
                    prev_coast_vals[prop] = val

                    extending = False
                    if (coast_dirs[prop] == 'rising' and
                            val > extreme_vals[prop]):
                        extending = True
                    elif (coast_dirs[prop] == 'falling' and
                          val < extreme_vals[prop]):
                        extending = True

                    if extending:
                        extreme_vals[prop] = val
                        extreme_times[prop] = elapsed
                        stable_counts[prop] = 0
                    else:
                        # Only count stability if we've seen at
                        # least one real change — otherwise we're
                        # reading stale sensor data
                        if seen_change.get(prop, False):
                            stable_counts[prop] += 1

                # Print status
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                parts = []
                for eid in sorted(sample.environments):
                    ep = sample.environments[eid]
                    if 'temperature' in ep:
                        if eid != 'ambient' and amb_t:
                            d = ep['temperature'] - amb_t
                            parts.append(
                                f"{eid}={fmt_t(ep['temperature'])}"
                                f" Δ{fmt_d(d)}")
                        else:
                            lbl = 'amb' if eid == 'ambient' else eid
                            parts.append(
                                f"{lbl}={fmt_t(ep['temperature'])}")
                    if 'humidity' in ep and eid != 'ambient':
                        parts.append(f"H={ep['humidity']:.1f}%")

                # Show coast status for primary property
                still_coasting = []
                for p in coast_dirs:
                    if stable_counts.get(p, 0) < STABLE_NEEDED:
                        still_coasting.append(p)
                if still_coasting:
                    status_parts = []
                    for p in sorted(still_coasting):
                        d = coast_dirs[p]
                        sc = stable_counts.get(p, 0)
                        marker = " ←" if p == primary_prop else ""
                        status_parts.append(
                            f"{p}:{d} {sc}/{STABLE_NEEDED}{marker}")
                    coast_status = "  " + " ".join(status_parts)
                else:
                    coast_status = "  all settled"
                print(f"  [{mins:02d}:{secs:02d}] COAST  "
                      f"{'  '.join(parts)}{coast_status}")

                # Coast ends when PRIMARY property has reversed
                # (3 consecutive non-extending readings after at
                # least one real value change).
                # Must also be past minimum coast time to avoid
                # false settlement from stale sensor data.
                if (primary_prop is not None and
                        elapsed >= min_coast_s and
                        seen_change.get(primary_prop, False) and
                        stable_counts.get(primary_prop, 0) >= STABLE_NEEDED):
                    ext = extreme_vals.get(primary_prop, 0)
                    ext_t = extreme_times.get(primary_prop, 0)
                    word = ('peaked' if primary_dir == 'rising'
                            else 'troughed')
                    if primary_prop == 'temperature':
                        print(f"\n  {primary_prop} {word} at "
                              f"{fmt_t(ext)} after {ext_t:.0f}s.")
                    elif primary_prop == 'humidity':
                        print(f"\n  {primary_prop} {word} at "
                              f"{ext:.1f}% after {ext_t:.0f}s.")
                    else:
                        print(f"\n  {primary_prop} {word} at "
                              f"{ext:.4f} after {ext_t:.0f}s.")
                    break
                elif (primary_prop is None and
                      not still_coasting and coast_dirs and
                      elapsed >= min_coast_s):
                    # No primary — fall back to all-settled
                    break

                time.sleep(args.sample_interval)

        except PhaseInterrupt:
            print(f"\n  Coast ended by user "
                  f"({len(coast_samples)} samples).")
            interrupt_count = 0
        except AbortExperiment:
            print("\n  ABORT.")
            signal.signal(signal.SIGINT, original_handler)
            sys.exit(1)

        # Compute coast characterization (summary + profile)
        coast_data = {}
        coast_peak = {}
        for prop in extreme_vals:
            if prop in shutoff_vals:
                overshoot = extreme_vals[prop] - shutoff_vals[prop]
                if abs(overshoot) > 0.01:
                    coast_data[prop] = {
                        'overshoot': round(overshoot, 4),
                        'duration': round(
                            extreme_times.get(prop, 0), 1),
                    }
                    coast_peak[prop] = {
                        'peak_value': round(extreme_vals[prop], 4),
                        'peak_elapsed_s': round(
                            extreme_times.get(prop, 0), 1),
                        'overshoot': round(overshoot, 4),
                    }

        if coast_data:
            print(f"  Coast characterization:")
            for prop, cd in coast_data.items():
                if prop == 'temperature':
                    ov_display = fmt_d(cd['overshoot'])
                elif prop == 'humidity':
                    ov_display = f"{cd['overshoot']:.1f}%"
                else:
                    ov_display = f"{cd['overshoot']:.4f}"
                print(f"    {prop}: overshoot={ov_display}, "
                      f"duration={cd['duration']:.0f}s")
        else:
            print(f"  No measurable coast "
                  f"(device has minimal inertia).")

        # ══════════════════════════════════════════════════════════════════
        # Phase 3: Decay — environment returning toward ambient
        # ══════════════════════════════════════════════════════════════════
        #
        # No fixed time cap.  Decay runs until:
        #   (a) The envelope estimator converges (RE < threshold), OR
        #   (b) The differential drops below 20% of starting value
        #       (signal is exhausted — further data is noise), OR
        #   (c) The estimator declares no signal, OR
        #   (d) User interrupt.
        #
        # A 45-minute sanity limit prevents infinite runs if the
        # environment never decays (e.g., ambient matches interior).

        print(f"\n── Phase 3: Decay ──")
        interrupt_count = 0

        decay_samples = []
        decay_start = time.time()
        max_decay_sanity = 2700  # 45 min sanity limit

        env_est = DecayEstimator(
            convergence_threshold=0.10,
            min_r_squared=0.95,
            min_samples=15,
            min_time_seconds=120.0,
            no_signal_time_seconds=300.0,
        )

        # Record starting differential for the 20% floor check
        initial_diff = None

        try:
            while True:
                elapsed = time.time() - decay_start
                if elapsed > max_decay_sanity:
                    print(f"\n  Decay sanity limit "
                          f"({max_decay_sanity // 60} min).")
                    break

                sample = read_all()
                decay_samples.append(sample)

                env_t = sample.environments.get(
                    dev_env, {}).get('temperature')
                amb_t = sample.environments.get(
                    'ambient', {}).get('temperature')
                d_est = None
                current_diff = None
                if env_t is not None and amb_t is not None:
                    current_diff = abs(env_t - amb_t)
                    if initial_diff is None:
                        initial_diff = current_diff
                    d_est = env_est.add(
                        sample.timestamp, env_t - amb_t)

                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                parts = []
                for eid in sorted(sample.environments):
                    ep = sample.environments[eid]
                    if 'temperature' in ep:
                        if eid != 'ambient' and amb_t:
                            d = ep['temperature'] - amb_t
                            parts.append(
                                f"{eid}={fmt_t(ep['temperature'])}"
                                f" Δ{fmt_d(d)}")
                        else:
                            lbl = ('amb' if eid == 'ambient'
                                   else eid)
                            parts.append(
                                f"{lbl}={fmt_t(ep['temperature'])}")
                    if 'humidity' in ep and eid != 'ambient':
                        parts.append(f"H={ep['humidity']:.1f}%")

                tau_str = ""
                if d_est and d_est.value > 0:
                    tau_str = (f"  τ={d_est.value:.0f}s"
                               f" RE={d_est.relative_error:.0%}")

                print(f"  [{mins:02d}:{secs:02d}] DECAY  "
                      f"{'  '.join(parts)}{tau_str}")

                # Check convergence
                if (d_est and
                        d_est.status == EstimateStatus.CONVERGED):
                    print(f"\n  Envelope converged: "
                          f"τ={d_est.value:.0f}s "
                          f"({d_est.value/60:.1f}min) "
                          f"±{d_est.std_error:.0f}s "
                          f"R²={d_est.r_squared:.4f}")
                    break

                # Check no-signal
                if (d_est and
                        d_est.status == EstimateStatus.NO_SIGNAL):
                    print(f"\n  {d_est.message}")
                    break

                # Check differential floor (20% of starting)
                if (initial_diff is not None and
                        initial_diff > 1.0 and
                        current_diff is not None and
                        current_diff < initial_diff * 0.20 and
                        elapsed > 120):
                    print(f"\n  Differential floor reached "
                          f"({fmt_d(current_diff)} < 20% of "
                          f"{fmt_d(initial_diff)}). "
                          f"Signal exhausted.")
                    break

                time.sleep(args.sample_interval)

        except PhaseInterrupt:
            print(f"\n  Decay ended by user "
                  f"({len(decay_samples)} samples).")
            interrupt_count = 0
        except AbortExperiment:
            print("\n  ABORT.")
            signal.signal(signal.SIGINT, original_handler)
            sys.exit(1)

        signal.signal(signal.SIGINT, original_handler)

        # ── Per-state analysis ─────────────────────────────
        print(f"\n── Analysis ({active_state}) ──")

        effects = _analyze(device_id, dev_env, rate_ests, decay_ests,
                           envelope_cal, display_unit)

        # Store per-state results
        all_state_results[active_state] = {
            'effects': effects,
            'coast_data': coast_data,
            'coast_profile': coast_profile,
            'coast_peak': coast_peak,
            'active_samples': active_samples,
            'coast_samples': coast_samples,
            'decay_samples': decay_samples,
            'safety_stopped': safety_stopped,
            'est_converged': est_converged,
        }
        last_decay_samples = decay_samples


    # ── Envelope update ──────────────────────────────────────────────
    if last_decay_samples and len(last_decay_samples) >= 10:
        had_env = envelope_cal is not None
        is_transfer = device_role in COOLING_ROLES
        _update_envelope(home, dev_env, device_id, last_decay_samples,
                         datetime.now(timezone.utc).isoformat(),
                         is_transfer_device=is_transfer)
        envelope_cal = _load_envelope_cal(home, dev_env)
        if not had_env and envelope_cal:
            print("  Envelope established.")


    # ── Write files ──────────────────────────────────────────────────
    cal_dir = home / 'calibration'
    cal_dir.mkdir(exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()

    device_cal = {
        'device_id': device_id,
        'environment': dev_env,
        'calibrated_at': now_iso,
        'power_draw_watts': round(power_watts, 2) if power_watts else None,
        'effects': {},
    }

    # Merge effects from all characterized states
    for state_name, sr in all_state_results.items():
        device_cal['effects'][state_name] = {}
        for e in sr['effects']:
            ek = e.environment
            if ek not in device_cal['effects'][state_name]:
                device_cal['effects'][state_name][ek] = {}
            ed = {
                'direction': e.direction,
                'rate_per_second': e.rate_per_second,
                'std_error': e.std_error,
                'type': e.effect_type,
            }
            if e.transfer_target:
                ed['target'] = e.transfer_target
            if e.conductance_delta is not None:
                ed['conductance_delta'] = e.conductance_delta
            if e.conductance_delta_se is not None:
                ed['conductance_delta_se'] = e.conductance_delta_se
            device_cal['effects'][state_name][ek][e.property_name] = ed

    # Coast profile and peak per state
    device_cal['coast_profile'] = {}
    device_cal['coast_peak'] = {}
    for state_name, sr in all_state_results.items():
        if sr['coast_profile']:
            device_cal['coast_profile'][state_name] = sr['coast_profile']
        if sr['coast_peak']:
            device_cal['coast_peak'][state_name] = sr['coast_peak']

    # Legacy coast summary from the highest state
    last_state = states_to_cal[-1]
    last_sr = all_state_results[last_state]
    if last_sr['coast_data']:
        device_cal['coast'] = last_sr['coast_data']

    if envelope_cal:
        device_cal['envelope_conductance'] = (
            envelope_cal.get('conductance', {}).get('temperature'))

    # Raw data summary per state
    device_cal['raw_data'] = {}
    for state_name, sr in all_state_results.items():
        a = sr['active_samples']
        c = sr['coast_samples']
        d = sr['decay_samples']
        device_cal['raw_data'][state_name] = {
            'active_samples': len(a),
            'active_duration_seconds': (
                a[-1].timestamp - a[0].timestamp if len(a) > 1 else 0),
            'coast_samples': len(c),
            'coast_duration_seconds': (
                c[-1].timestamp - c[0].timestamp if len(c) > 1 else 0),
            'decay_samples': len(d),
            'decay_duration_seconds': (
                d[-1].timestamp - d[0].timestamp if len(d) > 1 else 0),
            'safety_stopped': sr['safety_stopped'],
            'estimator_converged': sr['est_converged'],
        }

    dp = cal_dir / f'{device_id}.json'
    with open(dp, 'w') as f:
        json.dump(device_cal, f, indent=2)
    print(f"\n  Device calibration: {dp}")

    # Raw data file — all states
    rp = cal_dir / f'{device_id}_raw.json'
    rd = {
        'device_id': device_id,
        'calibrated_at': now_iso,
        'baseline': dict(baseline.environments),
        'states': {},
    }
    for state_name, sr in all_state_results.items():
        rd['states'][state_name] = {
            'active_samples': [
                {'timestamp': s.timestamp, 'environments': s.environments}
                for s in sr['active_samples']],
            'coast_samples': [
                {'timestamp': s.timestamp, 'environments': s.environments}
                for s in sr['coast_samples']],
            'decay_samples': [
                {'timestamp': s.timestamp, 'environments': s.environments}
                for s in sr['decay_samples']],
        }
    with open(rp, 'w') as f:
        json.dump(rd, f, indent=2)
    print(f"  Raw data: {rp}")
    print("\nDone.")


# ── Analysis from estimators ─────────────────────────────────────────

def _analyze(device_id, dev_env, rate_ests, decay_ests,
             envelope_cal, display_unit):
    effects = []

    for prop, rest in rate_ests.items():
        r = rest._evaluate()
        slope = r.value
        se = r.std_error

        if r.status == EstimateStatus.NO_SIGNAL:
            ub = abs(slope) + 2 * se
            effects.append(ObservedEffect(
                property_name=prop, environment=dev_env,
                direction='none', rate_per_second=0.0,
                std_error=round(se, 6), effect_type='bounded'))
            if prop == 'temperature':
                s = 9/5 if display_unit == 'F' else 1
                print(f"  {dev_env}.{prop}: no detectable effect "
                      f"(< {ub * s * 60:.3f}°{display_unit}/min)")
            else:
                print(f"  {dev_env}.{prop}: no detectable effect "
                      f"(< {ub * 60:.3f}/min)")
            continue

        if abs(slope) < 1e-10:
            continue

        direction = 'increase' if slope > 0 else 'decrease'

        # Transfer detection via decay estimator
        etype = 'energy'
        target = None
        cd = None
        cd_se = None

        if prop in decay_ests:
            dr = decay_ests[prop]._evaluate()
            if dr.status == EstimateStatus.CONVERGED and dr.value > 0:
                total_c = 1.0 / dr.value
                passive_c = None
                if envelope_cal:
                    passive_c = envelope_cal.get('conductance', {}).get(prop)
                if passive_c is not None:
                    cd = round(total_c - passive_c, 6)
                    if dr.std_error < float('inf') and dr.value > 0:
                        cd_se = round(dr.std_error / (dr.value ** 2), 6)
                    if cd > 0 and (cd_se is None or cd > 2 * cd_se):
                        etype = 'transfer'
                        target = 'ambient'
                    elif cd_se and abs(cd) < 2 * cd_se:
                        etype = 'bounded'

        effects.append(ObservedEffect(
            property_name=prop, environment=dev_env,
            direction=direction,
            rate_per_second=round(slope, 6),
            std_error=round(se, 6),
            effect_type=etype,
            transfer_target=target,
            conductance_delta=cd,
            conductance_delta_se=cd_se))

        # Print
        if prop == 'temperature':
            s = 9/5 if display_unit == 'F' else 1
            u = display_unit
            rs = f"{abs(slope)*s*60:.2f}°{u}/min"
            ss = f"±{se*s*60:.2f}"
        elif prop == 'humidity':
            rs = f"{abs(slope)*60:.2f}%/min"
            ss = f"±{se*60:.2f}"
        else:
            rs = f"{abs(slope)*60:.4f}/min"
            ss = f"±{se*60:.4f}"

        ts = etype.upper()
        if target:
            ts += f" → {target}"
        cs = ""
        if cd is not None:
            cs = f" (Δcond={cd:.6f}"
            if cd_se:
                cs += f" ±{cd_se:.6f}"
            cs += ")"

        print(f"  {dev_env}.{prop}: {direction} at {rs} {ss} [{ts}]{cs}")

    return effects


# ── Helper phase ─────────────────────────────────────────────────────

def _run_helper(helper_device, config, dev_env, args,
                read_all, check_safety, handle_interrupt,
                fmt_t, fmt_d, display_unit):
    from spriggler.devices.registry import get_device_driver

    print(f"\n── Phase 0.5: Building differential ──")
    print(f"  Using {helper_device['device_id']}...")

    hcfg = config['devices'][helper_device['device_id']]
    hdrv = get_device_driver(hcfg['driver'])(hcfg['driver_config'])

    min_dk = args.min_differential * (5/9 if display_unit == 'F' else 1)

    signal.signal(signal.SIGINT, handle_interrupt)

    hdrv.set_state('on')
    hstart = time.time()
    max_s = args.max_rise_minutes * 60

    try:
        while True:
            elapsed = time.time() - hstart
            if elapsed > max_s:
                print(f"\n  Max helper time.")
                break

            sample = read_all()
            ep = sample.environments.get(dev_env, {})
            ap = sample.environments.get('ambient', {})
            cdiff = abs(ep.get('temperature', 0) - ap.get('temperature', 0))

            m, s = int(elapsed // 60), int(elapsed % 60)
            if 'temperature' in ep:
                print(f"  [{m:02d}:{s:02d}] HELPER  "
                      f"int={fmt_t(ep['temperature'])}  Δamb={fmt_d(cdiff)}")

            if cdiff >= min_dk:
                print(f"\n  Differential: {fmt_d(cdiff)}")
                break

            if not check_safety(sample, hdrv, helper_device['device_id']):
                print("  Helper stopped by safety.")
                break

            time.sleep(args.sample_interval)
    except (PhaseInterrupt, AbortExperiment):
        pass

    hdrv.set_state('off')

    # Wait for thermal inertia peak
    print(f"  Helper off. Waiting for peak...")
    pk = 0
    stable = 0
    t0 = time.time()

    while time.time() - t0 < 300:
        sample = read_all()
        ct = sample.environments.get(dev_env, {}).get('temperature', 0)
        el = time.time() - t0

        if ct > pk:
            pk = ct
            stable = 0
        else:
            stable += 1

        m, s = int(el // 60), int(el % 60)
        st = "rising" if stable == 0 else f"stable {stable}"
        print(f"  [{m:02d}:{s:02d}] SETTLE  int={fmt_t(ct)}  ({st})")

        if stable >= 3:
            print(f"  Peak: {fmt_t(pk)}. Settled in {el:.0f}s.")
            break

        time.sleep(args.sample_interval)


# ── Utility functions ────────────────────────────────────────────────

def _extract_safety_limits(config):
    limits = {}
    for eid, es in config.get('safety', {}).get('environments', {}).items():
        limits[eid] = {}
        for prop, pl in es.get('limits', {}).items():
            limits[eid][prop] = {
                'absolute_min': pl.get('absolute_min'),
                'absolute_max': pl.get('absolute_max'),
            }
    return limits


def _print_safety_limits(limits, fmt_t, du):
    if not limits:
        print("  WARNING: No safety limits!", file=sys.stderr)
        return
    for eid, props in limits.items():
        for prop, vals in props.items():
            parts = []
            mx = vals.get('absolute_max')
            mn = vals.get('absolute_min')
            if mx is not None:
                parts.append(f"max={fmt_t(mx)}" if prop == 'temperature' else f"max={mx:.1f}")
            if mn is not None:
                parts.append(f"min={fmt_t(mn)}" if prop == 'temperature' else f"min={mn:.1f}")
            if parts:
                print(f"  Safety: {eid}.{prop} [{', '.join(parts)}]")


def _read_baseline_retry(read_all, required_envs, timeout=30, interval=2):
    baseline = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        sample = read_all()
        if baseline is None:
            baseline = sample
        else:
            for eid, props in sample.environments.items():
                if eid not in baseline.environments:
                    baseline.environments[eid] = props
                elif len(props) > len(baseline.environments[eid]):
                    baseline.environments[eid] = props
        if baseline and required_envs.issubset(set(baseline.environments)):
            if all('temperature' in baseline.environments.get(e, {})
                   for e in required_envs):
                return baseline
        time.sleep(interval)
    return baseline if baseline and baseline.environments else None


def _temp_diff(sample, dev_env):
    et = sample.environments.get(dev_env, {}).get('temperature', 0)
    at = sample.environments.get('ambient', {}).get('temperature', 0)
    return abs(et - at)


def _load_power_cal(home, device_id):
    p = home / 'calibration' / 'power.json'
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
        states = d.get('devices', {}).get(device_id, {}).get('states', {})
        # Find the highest-power non-off state
        best_watts = None
        for state_name, state_data in states.items():
            if state_name == 'off':
                continue
            w = state_data.get('watts_mean')
            if w is not None and (best_watts is None or w > best_watts):
                best_watts = w
        return best_watts
    except (json.JSONDecodeError, KeyError):
        return None


def _load_envelope_cal(home, env_id):
    p = home / 'calibration' / f'envelope_{env_id}.json'
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, KeyError):
        return None


def _find_helper_device(home, config, env_id, exclude):
    cal_dir = home / 'calibration'
    if not cal_dir.is_dir():
        return None
    for did, dcfg in config['devices'].items():
        if did == exclude or dcfg['environment'] != env_id:
            continue
        cp = cal_dir / f'{did}.json'
        if not cp.is_file():
            continue
        try:
            cd = json.loads(cp.read_text())
            # Find effects for any active state (not just 'on')
            all_effects = cd.get('effects', {})
            eff = {}
            for state_name, state_eff in all_effects.items():
                if state_name != 'off' and state_eff:
                    eff = state_eff
                    break
            te = eff.get(env_id, {}).get('temperature', {})
            if not te:
                te = eff.get('temperature', {})
            rr = (te.get('net_rise_rate_per_second')
                  or te.get('gross_rise_rate_per_second')
                  or te.get('rate_per_second', 0))
            if rr and rr > 0:
                return {'device_id': did, 'rise_rate': rr, 'cal_data': cd}
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def _update_envelope(home, env_id, device_id, decay_samples, now_iso,
                     is_transfer_device=False):
    """Fit decay and create/update envelope calibration.

    Transfer device decay observations are NEVER marked strong because
    their decay includes the device's own effect on conductance (e.g.
    a fan that was just running still has residual air exchange effects).
    Only energy device decays give clean passive envelope measurements.
    """
    ts = []
    for s in decay_samples:
        it = s.environments.get(env_id, {}).get('temperature')
        at = s.environments.get('ambient', {}).get('temperature')
        if it is not None and at is not None:
            ts.append(ThermalSample(timestamp=s.timestamp,
                                    interior=it, ambient=at))
    if len(ts) < 10:
        return

    try:
        result = fit_decay(ts)
    except ValueError:
        return

    print(f"\n  Decay fit: τ={result.tau:.0f}s ({result.tau/60:.1f}min) "
          f"RE={result.tau_relative_error:.1%} "
          f"R²={result.r_squared:.4f}")

    MAX_TAU_RE = 0.10   # Know τ to within 10%
    MIN_DIFF = 5.0      # Kelvin — need meaningful signal
    strong = (
            result.tau_relative_error <= MAX_TAU_RE
            and result.initial_differential >= MIN_DIFF
            and not is_transfer_device
    )

    if is_transfer_device:
        print(f"  Transfer device — decay includes device effect. "
              f"Not used for envelope.")
    elif not strong:
        reasons = []
        if result.tau_relative_error > MAX_TAU_RE:
            reasons.append(f"RE={result.tau_relative_error:.1%} > {MAX_TAU_RE:.0%}")
        if result.initial_differential < MIN_DIFF:
            reasons.append(f"diff={result.initial_differential:.1f}K < {MIN_DIFF:.0f}K")
        print(f"  Weak ({', '.join(reasons)}). Recording only.")

    obs = {
        'device': device_id,
        'conductance': result.conductance,
        'tau': result.tau,
        'tau_std_error': result.tau_std_error,
        'tau_relative_error': result.tau_relative_error,
        'r_squared': result.r_squared,
        'initial_differential': result.initial_differential,
        'calibrated_at': now_iso,
        'strong': strong,
    }

    cal_dir = home / 'calibration'
    cal_dir.mkdir(exist_ok=True)
    ep = cal_dir / f'envelope_{env_id}.json'

    existing = None
    if ep.is_file():
        try:
            existing = json.loads(ep.read_text())
        except (json.JSONDecodeError, KeyError):
            pass

    if existing is None:
        existing = {
            'environment': env_id,
            'calibrated_at': now_iso,
            'conductance': {'temperature': None},
            'time_constant': {'temperature': None},
            'derived_from_device': device_id if strong else None,
            'device_observations': [],
        }

    observations = existing.setdefault('device_observations', [])
    observations.append(obs)

    so = [o for o in observations if o.get('strong', False)]
    if so:
        cs = [o['conductance'] for o in so]
        mc = sum(cs) / len(cs)
        sc = 0.0
        if len(cs) > 1:
            sc = (sum((c - mc)**2 for c in cs) / (len(cs) - 1)) ** 0.5
        existing['conductance']['temperature'] = round(mc, 6)
        existing['time_constant']['temperature'] = round(1.0 / mc, 2)
        existing['consistency'] = {
            'mean': round(mc, 6), 'std': round(sc, 6),
            'strong_observations': len(so),
            'total_observations': len(observations),
        }

    with open(ep, 'w') as f:
        json.dump(existing, f, indent=2)
    print(f"  Envelope: {ep} ({len(observations)} total, {len(so)} strong)")