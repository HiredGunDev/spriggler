"""spriggler safety — safety limits and emergency controls."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def safety(ctx):
    """Safety limits and emergency controls.

    Safety is the highest priority.  These commands manage absolute
    limits, emergency shutdowns, and lockout state.  Safety limits
    are wider than target ranges — they represent "something is
    seriously wrong" boundaries, not "slightly out of comfort."

    \b
    Subcommands:
      spriggler safety show       Display current safety limits and status
      spriggler safety halt       Emergency: all energy devices OFF immediately
      spriggler safety lockout    Show/clear device lockouts
      spriggler safety test       Test safety shutdown behavior
    """
    pass


@safety.command("show")
@click.pass_context
def safety_show(ctx):
    """Display current safety limits and status.

    \b
    Shows per-environment:
      • Safety limits (min/max for each property)
      • Current values vs limits
      • Margin: how close current values are to limits
      • Active lockouts
      • Safety system status (armed / tripped / test mode)
    """
    in_development(
        command="spriggler safety show",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Safety dashboard showing limits, margins, and any active "
            "lockouts or trips.  Color-coded by margin:\n"
            "  • Green: >20% margin from limits\n"
            "  • Yellow: <20% margin\n"
            "  • Red: at or beyond limit (safety tripped)\n\n"
            "Safety limits are declared in config and are WIDER than "
            "target ranges.  Example: target temp 72-78°F, safety limits "
            "60-95°F.  The safety system trips only when something is "
            "genuinely wrong — a stuck heater, a dead sensor, etc."
        ),
        salvage_from_v04=[
            "safety/monitor.py — Safety limits, lockouts (keep concept)",
        ],
    )


@safety.command("halt")
@click.option(
    "--environment", "-e",
    help="Halt specific environment.  Default: all.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.pass_context
def safety_halt(ctx, environment, confirm):
    """Emergency halt: all energy devices OFF immediately.

    This is the panic button.  Turns off all energy-adding devices
    (heaters, humidifiers, lights NOT on schedule) immediately,
    regardless of controller state.  Transfer devices (fans) are
    left running if they have a favorable differential (ventilation
    is generally safe).

    \b
    After halt:
      • Daemon enters safe mode (no energy devices until cleared)
      • All energy devices commanded OFF
      • Status shows HALTED state
      • Requires 'spriggler start' or 'spriggler safety lockout clear'
        to resume normal operation

    \b
    This command works even if the daemon is not running — it
    communicates directly with device drivers.

    Does NOT require the daemon to be running.
    """
    in_development(
        command="spriggler safety halt",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Emergency shutdown.  Bypasses the daemon entirely — talks "
            "directly to device drivers to ensure devices are OFF even "
            "if the daemon is crashed or hung.\n\n"
            "Design: this command must work with minimal dependencies.  "
            "It should not require config parsing, calibration data, or "
            "a running daemon.  It reads device connection info from a "
            "minimal cache file and sends OFF commands directly.\n\n"
            "Safety invariant: energy-adding devices are the risk.  A "
            "stuck-on heater can start a fire.  A stuck-on humidifier "
            "can cause water damage.  Transfer devices (fans, pumps) "
            "are low-risk in either state."
        ),
        depends_on=[
            "Device drivers (KASA, VeSync) — direct access, no daemon",
        ],
    )


@safety.command("lockout")
@click.option(
    "--clear", "action",
    flag_value="clear",
    help="Clear all lockouts and return to normal operation.",
)
@click.option(
    "--device", "-d",
    help="Show or clear lockout for a specific device.",
)
@click.pass_context
def safety_lockout(ctx, action, device):
    """Show or clear device lockouts.

    Lockouts occur when a safety limit is reached or when actuator
    verification fails repeatedly.  A locked-out device is held OFF
    until manually cleared.

    \b
    Without --clear: shows all current lockouts with reason and timestamp.
    With --clear: clears lockout(s) and returns device to normal control.

    \b
    Examples:
      spriggler safety lockout                    Show all lockouts
      spriggler safety lockout --clear            Clear all lockouts
      spriggler safety lockout -d heater --clear  Clear heater lockout
    """
    in_development(
        command="spriggler safety lockout",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Lockout management.  Lockouts are the safety system's "
            "memory — once a device trips a safety limit, it stays "
            "locked out until a human explicitly clears it.\n\n"
            "This is intentional friction.  Automatic recovery from "
            "safety trips is dangerous — the condition that caused the "
            "trip may still exist.  A human must investigate and clear."
        ),
    )


@safety.command("test")
@click.option(
    "--environment", "-e",
    required=True,
    help="Environment to test safety on.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    help="Simulate safety trip without actually commanding devices.  "
         "Default: yes (always dry-run unless --live).",
)
@click.option(
    "--live",
    is_flag=True,
    help="Actually command devices during safety test.  Use with caution.",
)
@click.pass_context
def safety_test(ctx, environment, dry_run, live):
    """Test safety shutdown behavior.

    Simulates a safety limit breach and verifies the system responds
    correctly: energy devices OFF, lockout engaged, alerts generated.

    \b
    By default runs in dry-run mode — evaluates the safety logic but
    doesn't touch devices.  Use --live to actually cycle devices
    (useful for verifying that the halt command reaches devices).
    """
    in_development(
        command="spriggler safety test",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Safety system integration test.  Injects a simulated "
            "sensor reading that exceeds safety limits and verifies "
            "the correct response:\n"
            "  1. Safety monitor detects the breach\n"
            "  2. Energy devices commanded OFF\n"
            "  3. Lockout engaged\n"
            "  4. Alert generated and logged\n"
            "  5. Status shows SAFETY_TRIP state\n\n"
            "The --live mode actually cycles devices to verify end-to-end "
            "connectivity to actuators during a safety event."
        ),
    )
