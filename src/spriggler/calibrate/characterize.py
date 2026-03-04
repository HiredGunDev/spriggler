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
    helper_device = _find_helper_device(home, config, dev_env, device_id)

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
    if helper_device:
        print(f"  Helper: {helper_device['device_id']}")
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

    # ── Helper phase if needed ───────────────────────────────────────
    needs_helper = (
            helper_device is not None
            and _temp_diff(baseline, dev_env) < args.min_differential * 5/9
    )

    if needs_helper:
        _run_helper(
            helper_device, config, dev_env, args,
            read_all, check_safety, handle_interrupt,
            fmt_t, fmt_d, display_unit,
        )
        baseline = read_all()
        print(f"  New baseline:")
        for eid in sorted(baseline.environments):
            p = baseline.environments[eid]
            parts = []
            if 'temperature' in p:
                parts.append(f"T={fmt_t(p['temperature'])}")
            if 'humidity' in p:
                parts.append(f"H={p['humidity']:.1f}%")
            print(f"    {eid}: {', '.join(parts)}")

    # ══════════════════════════════════════════════════════════════════
    # Phase 1: Device ON — estimator-driven
    # ══════════════════════════════════════════════════════════════════
    print(f"\n── Phase 1: Device ON ({device_id}) ──")

    signal.signal(signal.SIGINT, handle_interrupt)
    interrupt_count = 0

    # During active phase, only check the limit the device could violate.
    # Heaters push temp up → check max only. Fans/coolers push down → check min only.
    device_role = dev_cfg.get('role', '')
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
    dev_driver.set_state('on')
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
    # Phase 2: Coast — device off, property still moving from inertia
    # ══════════════════════════════════════════════════════════════════
    print(f"\n── Phase 2: Device OFF (coast) ──")
    dev_driver.set_state('off')
    interrupt_count = 0

    coast_samples = []
    coast_start = time.time()
    max_coast_s = 300  # 5 min hard cap on coast

    # Determine coast direction per property from what the device was
    # doing during the active phase. Heater: temp was rising → coast
    # continues rising → track max. Cooler: temp was falling → coast
    # continues falling → track min.
    coast_dirs = {}  # {prop: 'rising' or 'falling'}
    for prop, est in rate_ests.items():
        r = est._evaluate()
        if r.value > 0:
            coast_dirs[prop] = 'rising'
        elif r.value < 0:
            coast_dirs[prop] = 'falling'

    # Track extremes per property
    extreme_vals = {}  # {prop: extreme_value (max if rising, min if falling)}
    stable_counts = {} # {prop: consecutive_non-extending}
    STABLE_NEEDED = 3

    # Record shutoff values for overshoot calculation
    shutoff_vals = {}
    last_active = active_samples[-1] if active_samples else None
    if last_active:
        for prop, val in last_active.environments.get(dev_env, {}).items():
            if prop not in IGNORE:
                shutoff_vals[prop] = val

    try:
        while True:
            elapsed = time.time() - coast_start
            if elapsed > max_coast_s:
                print(f"\n  Max coast time (5 min). Proceeding to decay.")
                break

            sample = read_all()
            coast_samples.append(sample)

            dev_props = sample.environments.get(dev_env, {})
            amb_t = sample.environments.get('ambient', {}).get('temperature')

            # Update extreme tracking
            for prop, val in dev_props.items():
                if prop in IGNORE or prop not in coast_dirs:
                    continue
                if prop not in extreme_vals:
                    extreme_vals[prop] = val
                    stable_counts[prop] = 0

                extending = False
                if coast_dirs[prop] == 'rising' and val > extreme_vals[prop]:
                    extending = True
                elif coast_dirs[prop] == 'falling' and val < extreme_vals[prop]:
                    extending = True

                if extending:
                    extreme_vals[prop] = val
                    stable_counts[prop] = 0
                else:
                    stable_counts[prop] += 1

            # Print
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            parts = []
            for eid in sorted(sample.environments):
                ep = sample.environments[eid]
                if 'temperature' in ep:
                    if eid != 'ambient' and amb_t:
                        d = ep['temperature'] - amb_t
                        parts.append(f"{eid}={fmt_t(ep['temperature'])} Δ{fmt_d(d)}")
                    else:
                        lbl = 'amb' if eid == 'ambient' else eid
                        parts.append(f"{lbl}={fmt_t(ep['temperature'])}")
                if 'humidity' in ep and eid != 'ambient':
                    parts.append(f"H={ep['humidity']:.1f}%")

            # Show coast status
            still_coasting = [
                p for p in coast_dirs
                if stable_counts.get(p, 0) < STABLE_NEEDED
            ]
            if still_coasting:
                status_parts = []
                for p in sorted(still_coasting):
                    d = coast_dirs[p]
                    sc = stable_counts.get(p, 0)
                    status_parts.append(f"{p}:{d} {sc}/{STABLE_NEEDED}")
                coast_status = "  " + " ".join(status_parts)
            else:
                coast_status = "  all settled"
            print(f"  [{mins:02d}:{secs:02d}] COAST  {'  '.join(parts)}{coast_status}")

            # Coast ends when ALL tracked properties have stabilized
            if not still_coasting and coast_dirs:
                for p in coast_dirs:
                    ext = extreme_vals.get(p, 0)
                    word = 'peaked' if coast_dirs[p] == 'rising' else 'troughed'
                    if p == 'temperature':
                        print(f"\n  {p} {word} at {fmt_t(ext)} after {elapsed:.0f}s.")
                    elif p == 'humidity':
                        print(f"\n  {p} {word} at {ext:.1f}% after {elapsed:.0f}s.")
                    else:
                        print(f"\n  {p} {word} at {ext:.4f} after {elapsed:.0f}s.")
                break

            time.sleep(args.sample_interval)

    except PhaseInterrupt:
        print(f"\n  Coast ended by user ({len(coast_samples)} samples).")
        interrupt_count = 0
    except AbortExperiment:
        print("\n  ABORT.")
        signal.signal(signal.SIGINT, original_handler)
        sys.exit(1)

    # Compute coast characterization
    coast_data = {}
    for prop in extreme_vals:
        if prop in shutoff_vals:
            overshoot = extreme_vals[prop] - shutoff_vals[prop]
            # Overshoot is in the direction the device was pushing.
            # Positive for heaters (temp continued rising).
            # Negative for coolers (temp continued falling).
            if abs(overshoot) > 0.01:  # ~0.02°F threshold
                coast_data[prop] = {
                    'overshoot': round(overshoot, 4),
                    'duration': round(time.time() - coast_start, 1),
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
            print(f"    {prop}: overshoot={ov_display}, duration={cd['duration']:.0f}s")
    else:
        print(f"  No measurable coast (device has minimal inertia).")

    # ══════════════════════════════════════════════════════════════════
    # Phase 3: Decay — temperature actually falling (estimator-driven)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n── Phase 3: Decay ──")
    interrupt_count = 0

    decay_samples = []
    decay_start = time.time()
    max_decay_s = args.max_decay_minutes * 60

    env_est = DecayEstimator(
        convergence_threshold=0.10,
        min_r_squared=0.95,
        min_samples=15,
        min_time_seconds=120.0,
        no_signal_time_seconds=300.0,
    )

    try:
        while True:
            elapsed = time.time() - decay_start
            if elapsed > max_decay_s:
                print(f"\n  Max decay time ({args.max_decay_minutes} min).")
                break

            sample = read_all()
            decay_samples.append(sample)

            env_t = sample.environments.get(dev_env, {}).get('temperature')
            amb_t = sample.environments.get('ambient', {}).get('temperature')
            d_est = None
            if env_t is not None and amb_t is not None:
                d_est = env_est.add(sample.timestamp, env_t - amb_t)

            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            parts = []
            for eid in sorted(sample.environments):
                ep = sample.environments[eid]
                if 'temperature' in ep:
                    if eid != 'ambient' and amb_t:
                        d = ep['temperature'] - amb_t
                        parts.append(f"{eid}={fmt_t(ep['temperature'])} Δ{fmt_d(d)}")
                    else:
                        lbl = 'amb' if eid == 'ambient' else eid
                        parts.append(f"{lbl}={fmt_t(ep['temperature'])}")
                if 'humidity' in ep and eid != 'ambient':
                    parts.append(f"H={ep['humidity']:.1f}%")

            tau_str = ""
            if d_est and d_est.value > 0:
                tau_str = f"  τ={d_est.value:.0f}s RE={d_est.relative_error:.0%}"

            print(f"  [{mins:02d}:{secs:02d}] DECAY  {'  '.join(parts)}{tau_str}")

            if d_est and d_est.status == EstimateStatus.CONVERGED:
                print(f"\n  Envelope converged: τ={d_est.value:.0f}s "
                      f"({d_est.value/60:.1f}min) ±{d_est.std_error:.0f}s "
                      f"R²={d_est.r_squared:.4f}")
                break

            if d_est and d_est.status == EstimateStatus.NO_SIGNAL:
                print(f"\n  {d_est.message}")
                break

            time.sleep(args.sample_interval)

    except PhaseInterrupt:
        print(f"\n  Decay ended by user ({len(decay_samples)} samples).")
        interrupt_count = 0
    except AbortExperiment:
        print("\n  ABORT.")
        signal.signal(signal.SIGINT, original_handler)
        sys.exit(1)

    signal.signal(signal.SIGINT, original_handler)

    # ── Envelope update ──────────────────────────────────────────────
    if decay_samples and len(decay_samples) >= 10:
        had_env = envelope_cal is not None
        is_transfer = device_role in COOLING_ROLES
        _update_envelope(home, dev_env, device_id, decay_samples,
                         datetime.now(timezone.utc).isoformat(),
                         is_transfer_device=is_transfer)
        envelope_cal = _load_envelope_cal(home, dev_env)
        if not had_env and envelope_cal:
            print("  Envelope established.")

    # ── Analysis ─────────────────────────────────────────────────────
    print(f"\n── Analysis ──")

    effects = _analyze(device_id, dev_env, rate_ests, decay_ests,
                       envelope_cal, display_unit)

    # ── Write files ──────────────────────────────────────────────────
    cal_dir = home / 'calibration'
    cal_dir.mkdir(exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()

    device_cal = {
        'device_id': device_id,
        'environment': dev_env,
        'calibrated_at': now_iso,
        'power_draw_watts': round(power_watts, 2) if power_watts else None,
        'effects': {'on': {}},
    }

    for e in effects:
        ek = e.environment
        if ek not in device_cal['effects']['on']:
            device_cal['effects']['on'][ek] = {}
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
        device_cal['effects']['on'][ek][e.property_name] = ed

    # Coast characterization
    if coast_data:
        device_cal['coast'] = coast_data

    if envelope_cal:
        device_cal['envelope_conductance'] = (
            envelope_cal.get('conductance', {}).get('temperature'))

    device_cal['raw_data'] = {
        'active_samples': len(active_samples),
        'active_duration_seconds': (
            active_samples[-1].timestamp - active_samples[0].timestamp
            if len(active_samples) > 1 else 0),
        'coast_samples': len(coast_samples),
        'coast_duration_seconds': (
            coast_samples[-1].timestamp - coast_samples[0].timestamp
            if len(coast_samples) > 1 else 0),
        'decay_samples': len(decay_samples),
        'decay_duration_seconds': (
            decay_samples[-1].timestamp - decay_samples[0].timestamp
            if len(decay_samples) > 1 else 0),
        'safety_stopped': safety_stopped,
        'estimator_converged': est_converged,
    }

    dp = cal_dir / f'{device_id}.json'
    with open(dp, 'w') as f:
        json.dump(device_cal, f, indent=2)
    print(f"\n  Device calibration: {dp}")

    rp = cal_dir / f'{device_id}_raw.json'
    rd = {
        'device_id': device_id,
        'calibrated_at': now_iso,
        'baseline': dict(baseline.environments),
        'active_samples': [
            {'timestamp': s.timestamp, 'environments': s.environments}
            for s in active_samples],
        'coast_samples': [
            {'timestamp': s.timestamp, 'environments': s.environments}
            for s in coast_samples],
        'decay_samples': [
            {'timestamp': s.timestamp, 'environments': s.environments}
            for s in decay_samples],
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
        return d.get('devices', {}).get(device_id, {}).get(
            'states', {}).get('on', {}).get('watts_mean')
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
            eff = cd.get('effects', {}).get('on', {})
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