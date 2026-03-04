"""Orchestrate full calibration of all devices.

Runs in correct dependency order:
    1. Power calibration (all devices, no dependencies)
    2. Energy devices (create their own signal, establish envelope)
       Ordered by power draw descending (bigger signal = better envelope fit)
    3. Transfer devices (need differential, use helpers)

Usage:
    spriggler calibrate all
"""

import json
import sys
from argparse import Namespace
from pathlib import Path

from spriggler.home import resolve_config, check_daemon, ConfigNotFoundError


# Role hints that indicate energy devices (push property away from ambient)
ENERGY_ROLES = {
    'heater', 'cooler', 'humidifier', 'dehumidifier', 'light',
    'heat', 'cool', 'lamp', 'grow_light',
}

# Role hints that indicate transfer devices (move property toward other env)
TRANSFER_ROLES = {
    'exhaust', 'intake', 'circulation', 'fan', 'vent',
    'exhaust_fan', 'intake_fan', 'transfer',
}


def run_calibrate_all(home: Path, args) -> None:
    """Run full calibration suite for all devices.

    Args:
        home: Spriggler home directory.
        args: Parsed CLI arguments.
    """
    # ── Check daemon ─────────────────────────────────────────────────
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

    devices = config['devices']

    # ── Classify and order devices ───────────────────────────────────
    energy_devices = []
    transfer_devices = []
    unknown_devices = []

    for dev_id, dev_cfg in devices.items():
        role = dev_cfg.get('role', '').lower()
        if role in ENERGY_ROLES:
            energy_devices.append(dev_id)
        elif role in TRANSFER_ROLES:
            transfer_devices.append(dev_id)
        else:
            # Unknown role — treat as energy (will discover via calibration)
            unknown_devices.append(dev_id)

    # Print plan
    print("═══════════════════════════════════════════════════════════════")
    print("  Spriggler Full Calibration")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"  Home: {home}")
    print(f"  Devices: {len(devices)}")
    print()
    if energy_devices:
        print(f"  Energy devices: {', '.join(energy_devices)}")
    if transfer_devices:
        print(f"  Transfer devices: {', '.join(transfer_devices)}")
    if unknown_devices:
        print(f"  Unknown (will discover): {', '.join(unknown_devices)}")
    print()
    print("  Order: power → energy devices → transfer devices")
    print("  Ctrl-C once = skip current phase. Twice = abort device.")
    print()

    # ══════════════════════════════════════════════════════════════════
    # Phase 1: Power calibration
    # ══════════════════════════════════════════════════════════════════
    print("═══════════════════════════════════════════════════════════════")
    print("  Phase 1: Power Calibration")
    print("═══════════════════════════════════════════════════════════════")
    print()

    from spriggler.calibrate.power import run_power_calibration

    power_args = Namespace(
        device=None,  # All devices
        force=True,   # Already checked daemon
        samples=args.samples,
        settle=args.settle,
        interval=args.power_interval,
    )

    try:
        run_power_calibration(home, power_args)
    except SystemExit:
        print("  Power calibration failed. Continuing...",
              file=sys.stderr)

    # ── Sort energy devices by power draw (highest first) ────────────
    power_data = _load_power_data(home)

    def power_sort_key(dev_id):
        """Higher power = lower sort key (comes first)."""
        watts = power_data.get(dev_id, 0)
        return -watts

    energy_devices.sort(key=power_sort_key)
    # Unknown devices go after known energy, before transfer
    unknown_devices.sort(key=power_sort_key)

    all_energy = energy_devices + unknown_devices

    if all_energy:
        print()
        print(f"  Energy device order: {', '.join(all_energy)}")
        for dev_id in all_energy:
            watts = power_data.get(dev_id, 0)
            if watts:
                print(f"    {dev_id}: {watts:.1f}W")

    # ══════════════════════════════════════════════════════════════════
    # Phase 2: Energy device characterization
    # ══════════════════════════════════════════════════════════════════
    from spriggler.calibrate.characterize import run_device_characterization

    for i, dev_id in enumerate(all_energy, 1):
        print()
        print("═══════════════════════════════════════════════════════════════")
        print(f"  Phase 2.{i}: Characterize {dev_id} (energy)")
        print("═══════════════════════════════════════════════════════════════")
        print()

        device_args = Namespace(
            device=dev_id,
            force=True,
            min_differential=args.min_differential,
            max_rise_minutes=args.max_rise_minutes,
            max_active_minutes=args.max_active_minutes,
            max_decay_minutes=args.max_decay_minutes,
            sample_interval=args.sample_interval,
        )

        try:
            run_device_characterization(home, device_args)
        except SystemExit:
            print(f"  Characterization of {dev_id} failed. Continuing...",
                  file=sys.stderr)
        except KeyboardInterrupt:
            print(f"\n  Skipping {dev_id}.")

    # ══════════════════════════════════════════════════════════════════
    # Phase 3: Transfer device characterization
    # ══════════════════════════════════════════════════════════════════
    for i, dev_id in enumerate(transfer_devices, 1):
        print()
        print("═══════════════════════════════════════════════════════════════")
        print(f"  Phase 3.{i}: Characterize {dev_id} (transfer)")
        print("═══════════════════════════════════════════════════════════════")
        print()

        device_args = Namespace(
            device=dev_id,
            force=True,
            min_differential=args.min_differential,
            max_rise_minutes=args.max_rise_minutes,
            max_active_minutes=args.max_active_minutes,
            max_decay_minutes=args.max_decay_minutes,
            sample_interval=args.sample_interval,
        )

        try:
            run_device_characterization(home, device_args)
        except SystemExit:
            print(f"  Characterization of {dev_id} failed. Continuing...",
                  file=sys.stderr)
        except KeyboardInterrupt:
            print(f"\n  Skipping {dev_id}.")

    # ══════════════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════════════
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  Calibration Complete")
    print("═══════════════════════════════════════════════════════════════")

    cal_dir = home / 'calibration'
    if cal_dir.is_dir():
        print()
        print("  Calibration files:")
        for path in sorted(cal_dir.iterdir()):
            if path.suffix == '.json' and '_raw' not in path.name:
                print(f"    {path.name}")

    # Check what the solver has to work with
    print()
    print("  Solver readiness:")
    envelope_files = list(cal_dir.glob('envelope_*.json')) if cal_dir.is_dir() else []
    for ef in envelope_files:
        try:
            data = json.loads(ef.read_text())
            env = data.get('environment', '?')
            cond = data.get('conductance', {}).get('temperature')
            tau = data.get('time_constant', {}).get('temperature')
            if tau:
                print(f"    Envelope {env}: τ={tau:.0f}s ({tau/60:.1f}min)")
        except (json.JSONDecodeError, KeyError):
            pass

    device_files = list(cal_dir.glob('*.json')) if cal_dir.is_dir() else []
    for df in sorted(device_files):
        if df.name.startswith('envelope_') or df.name.endswith('_raw.json') or df.name == 'power.json':
            continue
        try:
            data = json.loads(df.read_text())
            dev_id = data.get('device_id', df.stem)
            effects = data.get('effects', {}).get('on', {})
            effect_strs = []
            for env_id, props in effects.items():
                for prop, detail in props.items():
                    etype = detail.get('type', '?')
                    direction = detail.get('direction', '?')
                    effect_strs.append(f"{prop}:{direction}({etype})")
            watts = data.get('power_draw_watts', 0)
            watts_str = f" {watts:.0f}W" if watts else ""
            print(f"    {dev_id}:{watts_str} {', '.join(effect_strs)}")
        except (json.JSONDecodeError, KeyError):
            pass

    print()
    print("Done. Run 'spriggler --home <path> status' to verify, "
          "then start the daemon.")


def _load_power_data(home: Path) -> dict[str, float]:
    """Load power draw data for sorting. Returns {device_id: watts}."""
    power_path = home / 'calibration' / 'power.json'
    if not power_path.is_file():
        return {}
    try:
        data = json.loads(power_path.read_text())
        result = {}
        for dev_id, dev_data in data.get('devices', {}).items():
            watts = dev_data.get('states', {}).get('on', {}).get('watts_mean', 0)
            result[dev_id] = watts
        return result
    except (json.JSONDecodeError, KeyError):
        return {}