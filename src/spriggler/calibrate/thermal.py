"""Thermal calibration experiment runner.

Runs an interactive rise/decay experiment to characterize a device's
thermal contribution and the environment's envelope conductance.

Phases:
    1. Baseline: everything off, verify sensors are live, record starting state
    2. Rise: turn device on, watch temperature climb, record samples
    3. Decay: turn device off, watch exponential decay, record samples

The user can Ctrl-C during rise or decay to end that phase early and
move to the next. Two Ctrl-C's abort entirely.

Usage:
    spriggler calibrate thermal --device seedling_heater
"""

import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from spriggler.calibrate.thermal_fit import (
    ThermalSample,
    fit_decay,
    fit_rise,
)
from spriggler.home import resolve_config, check_daemon, ConfigNotFoundError
from spriggler.units import from_kelvin


class PhaseInterrupt(Exception):
    """Raised when user Ctrl-C's to end the current phase."""
    pass


class AbortExperiment(Exception):
    """Raised on second Ctrl-C to abort entirely."""
    pass


def run_thermal_calibration(home: Path, args) -> None:
    """Run thermal calibration for a single device.

    Args:
        home: Spriggler home directory.
        args: Parsed CLI arguments (device, force, etc.)
    """
    # ── Check for running daemon ─────────────────────────────────────
    if not args.force:
        status = check_daemon(home)
        if status.running:
            print(
                "ERROR: Spriggler daemon is currently running "
                f"(cycle {status.cycle}).\n"
                "Stop the daemon before calibrating, or use --force.",
                file=sys.stderr,
            )
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

    # ── Validate device selection ────────────────────────────────────
    device_id = args.device
    if device_id is None:
        print("Error: --device is required for thermal calibration.",
              file=sys.stderr)
        print("Available devices:", file=sys.stderr)
        for dev_id, dev_cfg in config['devices'].items():
            print(f"  {dev_id} ({dev_cfg.get('role', '?')})", file=sys.stderr)
        sys.exit(1)

    if device_id not in config['devices']:
        print(f"Error: device '{device_id}' not found in config.",
              file=sys.stderr)
        sys.exit(1)

    dev_cfg = config['devices'][device_id]
    env_id = dev_cfg['environment']

    # ── Find sensors ─────────────────────────────────────────────────
    interior_sensor_id = None
    ambient_sensor_id = None

    for sensor_id, sensor_cfg in config['sensors'].items():
        if sensor_cfg['environment'] == env_id:
            interior_sensor_id = sensor_id
        elif sensor_cfg['environment'] == 'ambient':
            ambient_sensor_id = sensor_id

    if interior_sensor_id is None:
        print(f"Error: no sensor found for environment '{env_id}'.",
              file=sys.stderr)
        sys.exit(1)

    if ambient_sensor_id is None:
        print("Error: no ambient sensor found.", file=sys.stderr)
        sys.exit(1)

    # ── Initialize drivers ───────────────────────────────────────────
    from spriggler.devices.registry import get_device_driver
    from spriggler.sensors.registry import get_sensor_driver

    dev_driver_cls = get_device_driver(dev_cfg['driver'])
    dev_driver = dev_driver_cls(dev_cfg['driver_config'])

    int_sensor_cfg = config['sensors'][interior_sensor_id]
    int_driver_cls = get_sensor_driver(int_sensor_cfg['driver'])
    int_driver = int_driver_cls(int_sensor_cfg['driver_config'])

    amb_sensor_cfg = config['sensors'][ambient_sensor_id]
    amb_driver_cls = get_sensor_driver(amb_sensor_cfg['driver'])
    amb_driver = amb_driver_cls(amb_sensor_cfg['driver_config'])

    # Load power calibration if available
    power_watts = _load_power_cal(home, device_id)

    # ── Print experiment plan ────────────────────────────────────────
    print(f"Thermal calibration: {device_id} ({dev_cfg.get('role', '?')})")
    print(f"  Environment: {env_id}")
    print(f"  Interior sensor: {interior_sensor_id}")
    print(f"  Ambient sensor: {ambient_sensor_id}")
    if power_watts:
        print(f"  Power draw: {power_watts:.1f}W (from power calibration)")
    else:
        print(f"  Power draw: unknown (run 'calibrate power' first for best results)")
    print(f"  Target rise: {args.rise_target}°{display_unit} above ambient")
    print(f"  Max rise time: {args.max_rise_minutes} minutes")
    print(f"  Max decay time: {args.max_decay_minutes} minutes")
    print(f"  Sample interval: {args.sample_interval}s")
    print()
    print("Phases: baseline → rise (heater on) → decay (heater off)")
    print("Ctrl-C once = end current phase. Twice = abort.")
    print()

    # ── Ctrl-C handler ───────────────────────────────────────────────
    interrupt_count = 0
    original_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(signum, frame):
        nonlocal interrupt_count
        interrupt_count += 1
        if interrupt_count >= 2:
            raise AbortExperiment()
        raise PhaseInterrupt()

    # ── Helper: read both sensors ────────────────────────────────────
    def read_sensors() -> ThermalSample | None:
        """Read both sensors and return a ThermalSample, or None."""
        int_reading = int_driver.read()
        amb_reading = amb_driver.read()

        if int_reading is None or amb_reading is None:
            return None

        int_temp = int_reading.get('temperature')
        amb_temp = amb_reading.get('temperature')

        if int_temp is None or amb_temp is None:
            return None

        return ThermalSample(
            timestamp=time.time(),
            interior=int_temp,
            ambient=amb_temp,
        )

    def fmt_temp(kelvin: float) -> str:
        """Format temperature in user's display unit."""
        return f"{from_kelvin(kelvin, display_unit):.1f}°{display_unit}"

    def fmt_diff(kelvin_diff: float) -> str:
        """Format a temperature differential in user's display unit."""
        if display_unit == 'F':
            return f"{kelvin_diff * 9/5:.1f}°{display_unit}"
        elif display_unit == 'C':
            return f"{kelvin_diff:.1f}°{display_unit}"
        else:
            return f"{kelvin_diff:.1f}K"

    # ── Phase 0: Baseline ────────────────────────────────────────────
    print("── Phase 0: Baseline ──")
    print("  Ensuring device is off, reading sensors...")

    dev_driver.set_state('off')
    time.sleep(2)

    baseline = read_sensors()
    if baseline is None:
        print("ERROR: Could not read sensors. Check BLE connectivity.",
              file=sys.stderr)
        sys.exit(1)

    print(f"  Interior: {fmt_temp(baseline.interior)}")
    print(f"  Ambient:  {fmt_temp(baseline.ambient)}")
    print(f"  Differential: {fmt_diff(baseline.differential)}")
    print()

    # Determine target: rise_target degrees above current interior
    # (in user units, convert to Kelvin)
    if display_unit == 'F':
        rise_target_k = args.rise_target * 5 / 9
    elif display_unit == 'C':
        rise_target_k = args.rise_target
    else:
        rise_target_k = args.rise_target

    target_interior = baseline.interior + rise_target_k

    # Safety check: will this exceed safety limits?
    safety_limits = config.get('safety', {}).get('environments', {}).get(env_id, {})
    abs_max = safety_limits.get('limits', {}).get('temperature', {}).get('absolute_max')
    if abs_max is not None:
        # abs_max is already in Kelvin (config loader converted it)
        safety_margin_k = 5 * 5/9 if display_unit == 'F' else 5  # 5 degrees margin
        if target_interior > abs_max - safety_margin_k:
            old_target = target_interior
            target_interior = abs_max - safety_margin_k
            rise_target_k = target_interior - baseline.interior
            print(f"  NOTE: Reduced rise target to stay below safety limit.")
            print(f"    Target interior: {fmt_temp(target_interior)}")
            print(f"    Safety max: {fmt_temp(abs_max)}")
            print()

    # ── Phase 1: Rise ────────────────────────────────────────────────
    print("── Phase 1: Rise ──")
    print(f"  Turning on {device_id}...")
    print(f"  Target: {fmt_temp(target_interior)} "
          f"(+{fmt_diff(rise_target_k)} above current)")
    print()

    rise_samples = []
    signal.signal(signal.SIGINT, handle_interrupt)
    interrupt_count = 0
    max_rise_seconds = args.max_rise_minutes * 60
    rise_start = time.time()

    dev_driver.set_state('on')

    try:
        while True:
            elapsed = time.time() - rise_start

            if elapsed > max_rise_seconds:
                print(f"\n  Max rise time reached ({args.max_rise_minutes} min).")
                break

            sample = read_sensors()
            if sample is not None:
                rise_samples.append(sample)
                diff = sample.interior - baseline.interior
                _print_sample_line(
                    "RISE", elapsed, sample, baseline.interior,
                    fmt_temp, fmt_diff, display_unit,
                )

                if sample.interior >= target_interior:
                    print(f"\n  Target reached! "
                          f"({fmt_temp(sample.interior)})")
                    break

            time.sleep(args.sample_interval)

    except PhaseInterrupt:
        print(f"\n  Rise phase ended by user ({len(rise_samples)} samples).")
        interrupt_count = 0

    except AbortExperiment:
        print("\n  ABORT — shutting down device.")
        dev_driver.set_state('off')
        signal.signal(signal.SIGINT, original_handler)
        sys.exit(1)

    # ── Phase 2: Decay ───────────────────────────────────────────────
    print()
    print("── Phase 2: Decay ──")
    print(f"  Turning off {device_id}...")
    print("  Watching temperature decay back toward ambient.")
    print()

    dev_driver.set_state('off')

    decay_samples = []
    interrupt_count = 0
    max_decay_seconds = args.max_decay_minutes * 60
    decay_start = time.time()

    # Small settling delay — device has thermal inertia
    time.sleep(3)

    try:
        while True:
            elapsed = time.time() - decay_start

            if elapsed > max_decay_seconds:
                print(f"\n  Max decay time reached ({args.max_decay_minutes} min).")
                break

            sample = read_sensors()
            if sample is not None:
                decay_samples.append(sample)
                _print_sample_line(
                    "DECAY", elapsed, sample, baseline.interior,
                    fmt_temp, fmt_diff, display_unit,
                )

                # Stop if we've decayed back close to baseline
                if (sample.differential < baseline.differential + 1.0
                        and len(decay_samples) > 10):
                    print(f"\n  Near baseline. Decay complete.")
                    break

            time.sleep(args.sample_interval)

    except PhaseInterrupt:
        print(f"\n  Decay phase ended by user ({len(decay_samples)} samples).")
        interrupt_count = 0

    except AbortExperiment:
        print("\n  ABORT.")
        signal.signal(signal.SIGINT, original_handler)
        sys.exit(1)

    signal.signal(signal.SIGINT, original_handler)

    # ── Analysis ─────────────────────────────────────────────────────
    print()
    print("── Analysis ──")

    if len(rise_samples) < 3:
        print("WARNING: Too few rise samples for analysis.", file=sys.stderr)
        rise_result = None
    else:
        print(f"  Rise: {len(rise_samples)} samples, "
              f"{rise_samples[-1].timestamp - rise_samples[0].timestamp:.0f}s")

    if len(decay_samples) < 5:
        print("WARNING: Too few decay samples for analysis.", file=sys.stderr)
        decay_result = None
    else:
        print(f"  Decay: {len(decay_samples)} samples, "
              f"{decay_samples[-1].timestamp - decay_samples[0].timestamp:.0f}s")

    # Fit decay first (gives us envelope conductance)
    decay_result = None
    if len(decay_samples) >= 5:
        try:
            decay_result = fit_decay(decay_samples)
            print()
            print(f"  Envelope time constant (τ): {decay_result.tau:.1f}s "
                  f"({decay_result.tau / 60:.1f} min)")
            print(f"  Envelope conductance: {decay_result.conductance:.6f} (1/s)")
            print(f"  R²: {decay_result.r_squared:.4f}")
            print(f"  Initial differential: {fmt_diff(decay_result.initial_differential)}")
            print(f"  Mean residual: {fmt_diff(decay_result.residuals_mean)}")
        except ValueError as e:
            print(f"  Decay fit failed: {e}", file=sys.stderr)

    # Fit rise (use envelope conductance if available)
    rise_result = None
    if len(rise_samples) >= 3:
        try:
            envelope_cond = decay_result.conductance if decay_result else None
            rise_result = fit_rise(
                rise_samples,
                envelope_conductance=envelope_cond,
                power_watts=power_watts,
            )
            print()
            print(f"  Gross rise rate: {fmt_diff(rise_result.gross_rise_rate)}/s "
                  f"({fmt_diff(rise_result.gross_rise_rate * 60)}/min)")
            print(f"  Net rise rate (device only): "
                  f"{fmt_diff(rise_result.net_rise_rate)}/s "
                  f"({fmt_diff(rise_result.net_rise_rate * 60)}/min)")
            print(f"  Peak differential: {fmt_diff(rise_result.peak_differential)}")
            if power_watts:
                print(f"  Electrical power: {rise_result.power_watts_electrical:.1f}W")
        except ValueError as e:
            print(f"  Rise fit failed: {e}", file=sys.stderr)

    # ── Write calibration files ──────────────────────────────────────
    cal_dir = home / 'calibration'
    cal_dir.mkdir(exist_ok=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Device calibration file
    if rise_result is not None:
        device_cal = {
            'device_id': device_id,
            'environment': env_id,
            'calibrated_at': now_iso,
            'ambient_during_cal': {
                'temperature': round(baseline.ambient, 2),
            },
            'power_draw_watts': round(power_watts, 2) if power_watts else None,
            'effects': {
                'on': {
                    'temperature': {
                        'gross_rise_rate_per_second': rise_result.gross_rise_rate,
                        'net_rise_rate_per_second': rise_result.net_rise_rate,
                        'peak_differential': rise_result.peak_differential,
                    }
                },
            },
            'envelope_conductance_observed': (
                decay_result.conductance if decay_result else None
            ),
            'raw_data': {
                'rise_samples': len(rise_samples),
                'rise_duration_seconds': rise_result.duration_seconds,
                'decay_samples': len(decay_samples) if decay_result else 0,
                'decay_duration_seconds': (
                    decay_result.duration_seconds if decay_result else 0
                ),
            },
        }

        device_path = cal_dir / f'{device_id}.json'
        with open(device_path, 'w') as f:
            json.dump(device_cal, f, indent=2)
        print(f"\n  Device calibration: {device_path}")

    # Envelope calibration file
    if decay_result is not None:
        envelope_cal = {
            'environment': env_id,
            'calibrated_at': now_iso,
            'conductance': {
                'temperature': decay_result.conductance,
            },
            'time_constant': {
                'temperature': decay_result.tau,
            },
            'derived_from_device': device_id,
            'fit_quality': {
                'r_squared': decay_result.r_squared,
                'sample_count': decay_result.sample_count,
                'residuals_mean_kelvin': decay_result.residuals_mean,
            },
            'ambient_during_cal': {
                'temperature': round(baseline.ambient, 2),
            },
            'differential_range': {
                'initial': decay_result.initial_differential,
                'final': decay_result.final_differential,
            },
        }

        envelope_path = cal_dir / f'envelope_{env_id}.json'

        # If envelope file already exists, merge — add this device's
        # observation to the consistency record
        if envelope_path.is_file():
            try:
                existing = json.loads(envelope_path.read_text())
                observations = existing.get('device_observations', [])
                observations.append({
                    'device': device_id,
                    'conductance': decay_result.conductance,
                    'r_squared': decay_result.r_squared,
                    'calibrated_at': now_iso,
                })
                existing['device_observations'] = observations

                # Recompute consistency
                conds = [o['conductance'] for o in observations]
                mean_c = sum(conds) / len(conds)
                if len(conds) > 1:
                    var = sum((c - mean_c)**2 for c in conds) / (len(conds) - 1)
                    std_c = var ** 0.5
                else:
                    std_c = 0.0
                existing['conductance']['temperature'] = round(mean_c, 6)
                existing['consistency'] = {
                    'mean': round(mean_c, 6),
                    'std': round(std_c, 6),
                    'observations': len(conds),
                }
                envelope_cal = existing
            except (json.JSONDecodeError, KeyError):
                # Corrupt file — overwrite
                envelope_cal['device_observations'] = [{
                    'device': device_id,
                    'conductance': decay_result.conductance,
                    'r_squared': decay_result.r_squared,
                    'calibrated_at': now_iso,
                }]
        else:
            envelope_cal['device_observations'] = [{
                'device': device_id,
                'conductance': decay_result.conductance,
                'r_squared': decay_result.r_squared,
                'calibrated_at': now_iso,
            }]

        with open(envelope_path, 'w') as f:
            json.dump(envelope_cal, f, indent=2)
        print(f"  Envelope calibration: {envelope_path}")

    # ── Save raw data ────────────────────────────────────────────────
    raw_path = cal_dir / f'{device_id}_raw.json'
    raw_data = {
        'device_id': device_id,
        'environment': env_id,
        'calibrated_at': now_iso,
        'baseline': {
            'interior': baseline.interior,
            'ambient': baseline.ambient,
            'differential': baseline.differential,
        },
        'rise_samples': [
            {
                'timestamp': s.timestamp,
                'interior': round(s.interior, 4),
                'ambient': round(s.ambient, 4),
            }
            for s in rise_samples
        ],
        'decay_samples': [
            {
                'timestamp': s.timestamp,
                'interior': round(s.interior, 4),
                'ambient': round(s.ambient, 4),
            }
            for s in decay_samples
        ],
    }
    with open(raw_path, 'w') as f:
        json.dump(raw_data, f, indent=2)
    print(f"  Raw data: {raw_path}")

    print()
    print("Done.")


def _print_sample_line(phase: str, elapsed: float, sample: ThermalSample,
                       baseline_interior: float,
                       fmt_temp, fmt_diff, display_unit: str) -> None:
    """Print a live sample reading."""
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    diff_from_baseline = sample.interior - baseline_interior
    print(
        f"  [{mins:02d}:{secs:02d}] {phase}  "
        f"int={fmt_temp(sample.interior)}  "
        f"amb={fmt_temp(sample.ambient)}  "
        f"Δamb={fmt_diff(sample.differential)}  "
        f"Δstart={fmt_diff(diff_from_baseline)}"
    )


def _load_power_cal(home: Path, device_id: str) -> float | None:
    """Load measured power for a device from calibration/power.json."""
    power_path = home / 'calibration' / 'power.json'
    if not power_path.is_file():
        return None

    try:
        data = json.loads(power_path.read_text())
        dev_data = data.get('devices', {}).get(device_id, {})
        on_state = dev_data.get('states', {}).get('on', {})
        return on_state.get('watts_mean')
    except (json.JSONDecodeError, KeyError):
        return None