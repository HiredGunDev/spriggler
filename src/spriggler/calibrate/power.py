"""Power calibration - measure actual wattage for each device.

Turns on each device, reads power draw from its power sensor
(typically a KASA strip outlet), and writes calibration/power.json.

This is the first step of calibration. The solver needs to know
actual watts per device to respect circuit amperage limits.

Usage:
    spriggler calibrate power
    spriggler calibrate power --device heater
    spriggler calibrate power --samples 10 --settle 15
"""

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from spriggler.home import resolve_config, check_daemon, ConfigNotFoundError


def run_power_calibration(home: Path, args) -> None:
    """Run power calibration for all (or one) device.

    Args:
        home: Spriggler home directory.
        args: Parsed CLI arguments (device, force, samples, settle, interval).
    """
    # ── Check for running daemon ─────────────────────────────────────
    if not args.force:
        status = check_daemon(home)
        if status.running:
            print(
                "ERROR: Spriggler daemon is currently running "
                f"(cycle {status.cycle}).\n"
                "Stop the daemon before calibrating, or use --force "
                "to override.\n"
                "Two processes controlling the same devices will conflict.",
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

    # ── Identify devices with power monitoring ───────────────────────
    devices_to_cal = _find_power_monitored_devices(config, args.device)

    if not devices_to_cal:
        if args.device:
            print(f"Device '{args.device}' not found or has no power sensor.",
                  file=sys.stderr)
        else:
            print("No devices with power sensors found in config.",
                  file=sys.stderr)
        sys.exit(1)

    # ── Initialize drivers ───────────────────────────────────────────
    from spriggler.devices.registry import get_device_driver

    print(f"Power calibration: {len(devices_to_cal)} device(s)")
    print(f"  Samples per state: {args.samples}")
    print(f"  Settle time: {args.settle}s")
    print(f"  Sample interval: {args.interval}s")
    print()

    results = {}
    errors = []

    for dev_id, dev_cfg in devices_to_cal.items():
        print(f"── {dev_id} ──")

        try:
            result = _calibrate_device_power(
                dev_id, dev_cfg, config,
                samples=args.samples,
                settle=args.settle,
                interval=args.interval,
            )
            results[dev_id] = result
        except Exception as e:
            print(f"  ERROR: {e}")
            errors.append((dev_id, str(e)))
            # Make sure device is off before moving to next
            try:
                _ensure_off(dev_id, dev_cfg, config)
            except Exception:
                pass

        print()

    # ── Write calibration/power.json ─────────────────────────────────
    cal_dir = home / 'calibration'
    cal_dir.mkdir(exist_ok=True)

    output = {
        'calibrated_at': datetime.now(timezone.utc).isoformat(),
        'devices': results,
    }

    if errors:
        output['errors'] = [
            {'device': dev_id, 'error': msg}
            for dev_id, msg in errors
        ]

    output_path = cal_dir / 'power.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"Results written to {output_path}")
    print(f"  Devices calibrated: {len(results)}")
    if errors:
        print(f"  Errors: {len(errors)}")

    # ── Summary ──────────────────────────────────────────────────────
    if results:
        print()
        print("Summary:")
        voltage = config['circuits'][
            next(iter(config['devices'].values()))['circuit']
        ]['voltage']

        for dev_id, dev_result in results.items():
            for state, measurements in dev_result.get('states', {}).items():
                watts = measurements['watts_mean']
                amps = watts / voltage
                print(f"  {dev_id}/{state}: "
                      f"{watts:.1f}W  {amps:.2f}A @ {voltage}V")


def _find_power_monitored_devices(config: dict,
                                  device_filter: str | None) -> dict:
    """Find devices that have power_sensor configurations.

    For now, any device controlled by a kasa_strip driver gets power
    monitoring for free — the same strip outlet that controls the device
    also reports power. Devices with explicit power_sensor blocks also
    qualify.

    Returns:
        {device_id: device_config} for devices with power monitoring.
    """
    result = {}
    for dev_id, dev_cfg in config.get('devices', {}).items():
        if device_filter and dev_id != device_filter:
            continue

        # Device has power if:
        # 1. Its driver is kasa_strip (control and power on same outlet)
        # 2. It has an explicit power_sensor block
        has_power = (
                dev_cfg.get('driver') == 'kasa_strip'
                or 'power_sensor' in dev_cfg
        )
        if has_power:
            result[dev_id] = dev_cfg

    return result


def _calibrate_device_power(dev_id: str, dev_cfg: dict, config: dict,
                            samples: int, settle: float,
                            interval: float) -> dict:
    """Calibrate power for a single device.

    Returns dict with measured power per state.
    """
    from spriggler.devices.registry import get_device_driver

    # Instantiate the device driver
    driver_cls = get_device_driver(dev_cfg['driver'])
    driver = driver_cls(dev_cfg['driver_config'])

    states = driver.get_available_states()
    print(f"  States: {states}")

    state_results = {}

    for state in states:
        if state == 'off':
            # Measure standby power too
            print(f"  Measuring standby (off)...")
            driver.set_state('off')
            time.sleep(settle)
            readings = _take_power_samples(driver, samples, interval)
            state_results['off'] = _summarize_samples(readings)
            print(f"    Standby: {state_results['off']['watts_mean']:.1f}W")
        else:
            print(f"  Turning on: {state}...")
            driver.set_state(state)
            print(f"    Settling for {settle}s...")
            time.sleep(settle)

            readings = _take_power_samples(driver, samples, interval)
            state_results[state] = _summarize_samples(readings)

            watts = state_results[state]['watts_mean']
            print(f"    Power: {watts:.1f}W "
                  f"(stddev: {state_results[state]['watts_stddev']:.1f}W)")

    # Return to off state
    print(f"  Turning off...")
    driver.set_state('off')

    return {
        'driver': dev_cfg['driver'],
        'driver_config': dev_cfg['driver_config'],
        'environment': dev_cfg['environment'],
        'circuit': dev_cfg['circuit'],
        'role': dev_cfg.get('role', 'unknown'),
        'states': state_results,
    }


def _take_power_samples(driver, count: int,
                        interval: float) -> list[dict]:
    """Take multiple power readings from a device driver.

    Returns list of dicts with watts (and optionally volts, amps).
    """
    readings = []
    for i in range(count):
        watts = driver.get_power()
        reading = {'watts': watts if watts is not None else 0.0}
        readings.append(reading)
        if i < count - 1:
            time.sleep(interval)
    return readings


def _summarize_samples(readings: list[dict]) -> dict:
    """Compute mean and stddev from power samples."""
    watts_values = [r['watts'] for r in readings]
    n = len(watts_values)

    if n == 0:
        return {
            'watts_mean': 0.0,
            'watts_stddev': 0.0,
            'samples': 0,
        }

    mean = sum(watts_values) / n

    if n > 1:
        variance = sum((w - mean) ** 2 for w in watts_values) / (n - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0

    return {
        'watts_mean': round(mean, 2),
        'watts_stddev': round(stddev, 2),
        'watts_min': round(min(watts_values), 2),
        'watts_max': round(max(watts_values), 2),
        'samples': n,
        'raw': watts_values,
    }


def _ensure_off(dev_id: str, dev_cfg: dict, config: dict) -> None:
    """Best-effort turn off a device."""
    from spriggler.devices.registry import get_device_driver
    try:
        driver_cls = get_device_driver(dev_cfg['driver'])
        driver = driver_cls(dev_cfg['driver_config'])
        driver.set_state('off')
    except Exception:
        pass