"""Spriggler CLI - tools for monitoring and managing the daemon.

Subcommands:
    display     Live terminal dashboard showing daemon state
    status      One-shot dump of current status (future)
    explain     Explain solver decisions for a cycle (future)

All commands read from the state directory (~/.spriggler/ by default).
"""

import argparse
import sys

from spriggler.state import resolve_state_dir


def main():
    parser = argparse.ArgumentParser(
        prog='spriggler',
        description='Spriggler environmental control system',
    )
    parser.add_argument(
        '--state-dir',
        help='State directory (default: ~/.spriggler/)',
    )

    subparsers = parser.add_subparsers(dest='command', help='Command')

    # ── display ──────────────────────────────────────────────────────
    display_parser = subparsers.add_parser(
        'display',
        help='Live terminal dashboard',
    )
    display_parser.add_argument(
        '--interval', '-i', type=float, default=2.0,
        help='Refresh interval in seconds (default: 2.0)',
    )

    # ── status (future) ─────────────────────────────────────────────
    subparsers.add_parser(
        'status',
        help='One-shot status dump (not yet implemented)',
    )

    # ── explain (future) ─────────────────────────────────────────────
    subparsers.add_parser(
        'explain',
        help='Explain solver decisions (not yet implemented)',
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    state_dir = resolve_state_dir(args.state_dir)

    if args.command == 'display':
        from spriggler.cli.display import run_display
        run_display(state_dir, interval=args.interval)

    elif args.command == 'status':
        print("Not yet implemented. Use 'spriggler display' for now.")
        sys.exit(1)

    elif args.command == 'explain':
        print("Not yet implemented.")
        sys.exit(1)