"""spriggler stop — shut down the controller daemon."""

import click

from spriggler.cli._style import in_development


@click.command()
@click.option(
    "--graceful/--immediate",
    default=True,
    help="Graceful: finish current cycle, safe-state devices.  "
         "Immediate: stop now, safe-state devices.  Default: graceful.",
)
@click.option(
    "--leave-devices",
    is_flag=True,
    help="Don't touch device states on shutdown.  Devices remain in "
         "their current state.  USE WITH CAUTION — heaters will stay on.",
)
@click.pass_context
def stop(ctx, graceful, leave_devices):
    """Stop the Spriggler controller daemon.

    By default, performs a graceful shutdown: completes the current
    control cycle, then puts all energy-adding devices into their safe
    state (off) and leaves transfer devices in their current state.

    \b
    Shutdown sequence (graceful):
      1. Signal daemon to stop after current cycle
      2. Wait for cycle completion (up to 30s)
      3. Safe-state all energy devices (off)
      4. Write final status.json with state=stopped
      5. Exit

    \b
    Shutdown sequence (immediate):
      1. Safe-state all energy devices immediately
      2. Write final status.json with state=stopped
      3. Exit

    \b
    Examples:
      spriggler stop                Stop gracefully
      spriggler stop --immediate    Stop NOW, safe-state devices
      spriggler stop --leave-devices  Stop daemon, leave devices as-is
    """
    in_development(
        command="spriggler stop",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Signals the running daemon to shut down.  Communicates via "
            "PID file or Unix domain socket.\n\n"
            "The safe-state behavior is important: when the controller "
            "stops, energy-adding devices (heaters, humidifiers, lights "
            "that aren't on a schedule) are turned OFF.  Transfer devices "
            "(fans, pumps) are left in current state since they can't "
            "cause damage in either direction.\n\n"
            "The --leave-devices flag exists for cases where you're "
            "restarting the controller and don't want a gap where devices "
            "cycle off and on.  It's intentionally scary-named to "
            "discourage casual use."
        ),
        notes=(
            "IPC mechanism: PID file at $config_dir/spriggler.pid plus a "
            "Unix domain socket for richer communication.  The stop command "
            "sends SIGTERM for graceful, SIGKILL as fallback after timeout.\n\n"
            "The --leave-devices flag must log a prominent warning.  A heater "
            "left on without a controller watching it is a fire risk."
        ),
        depends_on=[
            "spriggler start  (daemon must be running)",
        ],
    )
