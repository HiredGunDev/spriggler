"""Spriggler CLI - command-line interface for Spriggler operations.

Usage:
    spriggler calibrate power [--home PATH] [--device DEVICE] [--force]
    spriggler status [--home PATH]
    spriggler check [--home PATH]

The --home flag specifies the Spriggler installation directory.
If omitted, falls back to SPRIGGLER_HOME env var, then current directory.
"""

import argparse
import sys

from spriggler.home import resolve_home, HomeNotFoundError


def main():
    """Main entry point for the spriggler CLI."""
    parser = argparse.ArgumentParser(
        prog='spriggler',
        description='Spriggler environmental controller CLI',
    )
    parser.add_argument(
        '--home',
        default=None,
        help='Spriggler home directory (default: $SPRIGGLER_HOME or cwd)',
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # ── calibrate ────────────────────────────────────────────────────
    cal_parser = subparsers.add_parser(
        'calibrate',
        help='Run calibration procedures',
    )
    cal_sub = cal_parser.add_subparsers(
        dest='calibrate_command',
        help='Calibration subcommand',
    )

    # calibrate all (full suite)
    all_parser = cal_sub.add_parser(
        'all',
        help='Run full calibration suite (power + all devices)',
    )
    all_parser.add_argument(
        '--force',
        action='store_true',
        help='Run even if daemon is active',
    )
    all_parser.add_argument(
        '--samples',
        type=int,
        default=5,
        help='Power samples per state (default: 5)',
    )
    all_parser.add_argument(
        '--settle',
        type=float,
        default=10.0,
        help='Power calibration settle time (default: 10)',
    )
    all_parser.add_argument(
        '--power-interval',
        type=float,
        default=2.0,
        help='Power calibration sample interval (default: 2)',
    )
    all_parser.add_argument(
        '--min-differential',
        type=float,
        default=10.0,
        help='Min differential for transfer devices (default: 10)',
    )
    all_parser.add_argument(
        '--max-rise-minutes',
        type=float,
        default=30.0,
        help='Max helper rise time (default: 30)',
    )
    all_parser.add_argument(
        '--max-active-minutes',
        type=float,
        default=30.0,
        help='Max device-on time (default: 30)',
    )
    all_parser.add_argument(
        '--max-decay-minutes',
        type=float,
        default=45.0,
        help='Max decay observation time (default: 45)',
    )
    all_parser.add_argument(
        '--sample-interval',
        type=float,
        default=15.0,
        help='Sensor sample interval (default: 15)',
    )

    # calibrate power
    power_parser = cal_sub.add_parser(
        'power',
        help='Measure power draw for all devices',
    )
    power_parser.add_argument(
        '--device',
        default=None,
        help='Calibrate a single device (by config ID)',
    )
    power_parser.add_argument(
        '--force',
        action='store_true',
        help='Run even if daemon is active',
    )
    power_parser.add_argument(
        '--samples',
        type=int,
        default=5,
        help='Number of power samples per state (default: 5)',
    )
    power_parser.add_argument(
        '--settle',
        type=float,
        default=10.0,
        help='Seconds to wait for device to stabilize (default: 10)',
    )
    power_parser.add_argument(
        '--interval',
        type=float,
        default=2.0,
        help='Seconds between power samples (default: 2)',
    )

    # calibrate thermal
    thermal_parser = cal_sub.add_parser(
        'thermal',
        help='Characterize device thermal contribution and envelope',
    )
    thermal_parser.add_argument(
        '--device',
        default=None,
        help='Device to calibrate (required)',
    )
    thermal_parser.add_argument(
        '--force',
        action='store_true',
        help='Run even if daemon is active',
    )
    thermal_parser.add_argument(
        '--rise-target',
        type=float,
        default=15.0,
        help='Target rise in display units above current (default: 15)',
    )
    thermal_parser.add_argument(
        '--max-rise-minutes',
        type=float,
        default=45.0,
        help='Maximum rise phase duration in minutes (default: 45)',
    )
    thermal_parser.add_argument(
        '--max-decay-minutes',
        type=float,
        default=90.0,
        help='Maximum decay phase duration in minutes (default: 90)',
    )
    thermal_parser.add_argument(
        '--sample-interval',
        type=float,
        default=15.0,
        help='Seconds between sensor readings (default: 15)',
    )

    # calibrate device (general characterization)
    device_parser = cal_sub.add_parser(
        'device',
        help='Characterize device effects on all properties',
    )
    device_parser.add_argument(
        '--device',
        default=None,
        help='Device to calibrate (required)',
    )
    device_parser.add_argument(
        '--force',
        action='store_true',
        help='Run even if daemon is active',
    )
    device_parser.add_argument(
        '--min-differential',
        type=float,
        default=10.0,
        help='Minimum differential needed in display units (default: 10)',
    )
    device_parser.add_argument(
        '--max-rise-minutes',
        type=float,
        default=30.0,
        help='Max time for helper device to build differential (default: 30)',
    )
    device_parser.add_argument(
        '--max-active-minutes',
        type=float,
        default=30.0,
        help='Max time with device on (default: 30)',
    )
    device_parser.add_argument(
        '--max-decay-minutes',
        type=float,
        default=45.0,
        help='Max decay observation time (default: 45)',
    )
    device_parser.add_argument(
        '--sample-interval',
        type=float,
        default=15.0,
        help='Seconds between sensor readings (default: 15)',
    )

    # ── display ────────────────────────────────────────────────────────
    display_parser = subparsers.add_parser(
        'display',
        help='Live terminal dashboard',
    )
    display_parser.add_argument(
        '--interval', '-i', type=float, default=2.0,
        help='Refresh interval in seconds (default: 2.0)',
    )

    # ── status ───────────────────────────────────────────────────────
    subparsers.add_parser(
        'status',
        help='Show current system status',
    )

    # ── check ────────────────────────────────────────────────────────
    subparsers.add_parser(
        'check',
        help='Validate configuration',
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # Resolve home directory
    try:
        home = resolve_home(args.home)
    except HomeNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Dispatch
    if args.command == 'calibrate':
        if args.calibrate_command == 'all':
            from spriggler.calibrate.suite import run_calibrate_all
            run_calibrate_all(home, args)
        elif args.calibrate_command == 'power':
            from spriggler.calibrate.power import run_power_calibration
            run_power_calibration(home, args)
        elif args.calibrate_command == 'thermal':
            from spriggler.calibrate.thermal import run_thermal_calibration
            run_thermal_calibration(home, args)
        elif args.calibrate_command == 'device':
            from spriggler.calibrate.characterize import run_device_characterization
            run_device_characterization(home, args)
        else:
            cal_parser.print_help()
            sys.exit(1)
    elif args.command == 'display':
        from spriggler.state import resolve_state_dir
        state_dir = resolve_state_dir(None)  # Default state dir
        from spriggler.cli.display import run_display
        run_display(state_dir, interval=args.interval)
    elif args.command == 'status':
        _cmd_status(home)
    elif args.command == 'check':
        _cmd_check(home)
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_status(home):
    """Show current daemon and system status."""
    from spriggler.home import check_daemon
    status = check_daemon(home)

    if status.running:
        from datetime import datetime, timezone
        last = datetime.fromtimestamp(status.last_seen, tz=timezone.utc)
        print(f"Daemon: RUNNING (cycle {status.cycle}, "
              f"last seen {last.strftime('%H:%M:%S %Z')})")
    else:
        print("Daemon: NOT RUNNING")

    # If status.json exists, show environment data
    import json
    status_path = home / 'status.json'
    if status_path.is_file():
        try:
            data = json.loads(status_path.read_text())
            envs = data.get('environments', {})
            for env_id, env_data in envs.items():
                temp = env_data.get('temperature', {}).get('display', '--')
                hum = env_data.get('humidity', {}).get('display', '--')
                print(f"  {env_id}: {temp}  {hum}")
        except (json.JSONDecodeError, OSError):
            pass


def _cmd_check(home):
    """Validate configuration."""
    from spriggler.home import resolve_config, ConfigNotFoundError
    try:
        config_path = resolve_config(home)
    except ConfigNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    from spriggler.config.loader import load_config
    try:
        config = load_config(str(config_path))
        print(f"Config OK: {config.get('name', 'unnamed')}")
        print(f"  Environments: {', '.join(config['environments'].keys())}")
        print(f"  Sensors: {len(config['sensors'])}")
        print(f"  Devices: {len(config['devices'])}")
        print(f"  Circuits: {len(config['circuits'])}")
    except Exception as e:
        print(f"Config ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()