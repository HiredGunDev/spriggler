"""spriggler start — launch the controller daemon."""

import click

from spriggler.cli._style import in_development


@click.command()
@click.option(
    "--foreground", "-f",
    is_flag=True,
    help="Run in foreground (don't daemonize).  Useful for debugging.",
)
@click.option(
    "--environment", "-e",
    multiple=True,
    help="Control only named environment(s).  May be repeated.  "
         "Default: all configured environments.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Evaluate control decisions but don't actuate devices.  "
         "Logs what WOULD happen.",
)
@click.option(
    "--safe-mode",
    is_flag=True,
    help="Start in safe mode: energy-adding devices off, transfers "
         "evaluated but conservative.  Useful after config changes.",
)
@click.pass_context
def start(ctx, foreground, environment, dry_run, safe_mode):
    """Start the Spriggler controller daemon.

    Loads config and calibration data, connects to sensors and devices,
    and begins the control loop.  Runs as a background daemon by default;
    use --foreground for interactive debugging.

    \b
    The control loop runs one cycle per second (configurable).  Each cycle:
      1. Read all sensor states, classify freshness
      2. Convert derived → fundamental quantities (physics plugins)
      3. Fuse sensor readings (Kalman filter)
      4. Evaluate energy devices (hysteresis + coast compensation)
      5. Evaluate transfer devices (differential-based)
      6. Check schedule anticipation (Phase 2+)
      7. Resolve conflicts, select actuator commands
      8. Command devices, start verification timers
      9. Write status.json for UI consumption
      10. Log structured cycle data

    \b
    Examples:
      spriggler start                    Start daemon, all environments
      spriggler start -f                 Foreground mode
      spriggler start -e seedling        Control only the seedling pod
      spriggler start --dry-run -f       See decisions without acting
    """
    in_development(
        command="spriggler start",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Launches the main control daemon.  This is the heart of "
            "Spriggler — the control loop that reads sensors, makes "
            "physics-informed decisions, and commands actuators.\n\n"
            "The daemon writes runtime state to status.json for the "
            "web UI (Spriggle) and any external monitoring.  It uses "
            "structured JSON logging for all events."
        ),
        notes=(
            "The control loop is written from scratch for v0.5.  The v0.4 "
            "daemon's loop had the trajectory planner baked in and is not "
            "salvageable.  The new loop is simple: read → convert → fuse → "
            "decide → command → log.  Each step is a clean function.\n\n"
            "The --foreground flag is critical for development — it keeps "
            "stdout attached and lets you Ctrl-C cleanly.\n\n"
            "The --dry-run flag evaluates everything but sends no commands "
            "to devices.  The log shows what WOULD have been commanded.  "
            "Essential for validating control logic before going live.\n\n"
            "Safe mode starts with all energy-adding devices OFF and only "
            "allows transfer devices (fans/pumps) that have a favorable "
            "differential.  Useful after changing config or recalibrating."
        ),
        depends_on=[
            "spriggler config validate  (valid config required)",
            "spriggler calibrate run    (calibration data required)",
            "Sensor drivers (govee BLE, wired, etc.)",
            "Device drivers (KASA, VeSync, etc.)",
            "Physics plugin library",
            "Kalman filter (sensor fusion)",
            "Hysteresis controller (energy devices)",
            "Differential controller (transfer devices)",
        ],
        salvage_from_v04=[
            "sensors/govee.py  — BLE scanner with _sample_time",
            "devices/kasa.py   — KASA discovery and connection manager",
            "devices/vesync.py — Rate limiter wrapper",
            "devices/vesync_device.py — Fire-and-forget pattern",
            "struct_log.py     — Structured JSON logger",
        ],
    )
