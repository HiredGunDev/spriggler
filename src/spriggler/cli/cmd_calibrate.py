"""spriggler calibrate — discover device and environment characteristics."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def calibrate(ctx):
    """Calibrate devices and environments.

    Calibration measures how your devices actually affect the environment:
    rates of change, coast/overshoot profiles after shutoff, and passive
    conductance (how fast the environment drifts toward ambient).

    All calibration works in fundamental physical quantities — temperature
    in Kelvin (displayed as °F/°C), moisture as absolute humidity (g/m³).
    This eliminates phantom cross-effects that plagued v0.4.

    \b
    Subcommands:
      spriggler calibrate run       Run full calibration sequence
      spriggler calibrate device    Calibrate a single device
      spriggler calibrate passive   Measure passive conductance only
      spriggler calibrate show      Display current calibration data
      spriggler calibrate export    Export calibration to file
      spriggler calibrate import    Import calibration from file
    """
    pass


@calibrate.command("run")
@click.option(
    "--environment", "-e",
    required=True,
    help="Environment to calibrate.",
)
@click.option(
    "--device", "-d",
    multiple=True,
    help="Calibrate specific device(s) only.  May be repeated.  "
         "Default: all devices in the environment.",
)
@click.option(
    "--include-passive/--skip-passive",
    default=True,
    help="Include passive conductance measurement.  Default: include.",
)
@click.option(
    "--settle-time",
    type=int,
    default=None,
    help="Override settle time (seconds) between calibration phases.  "
         "Default: auto-detect from sensor delivery interval.",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Resume an interrupted calibration from the last checkpoint.",
)
@click.pass_context
def calibrate_run(ctx, environment, device, include_passive, settle_time, resume):
    """Run calibration sequence for an environment.

    \b
    Calibration sequence per device, per state:
      1. Pre-condition: wait for environment to reach baseline
      2. Activate device state, measure rate on intended properties
         (fundamental quantities only — %RH converted to absolute
         humidity at the sensor boundary)
      3. Deactivate, measure coast profile (post-shutoff trajectory)
      4. Wait for return to baseline
      5. Repeat for each non-off state of graduated devices

    \b
    After all devices: measure passive conductance (environment → ambient
    decay with all devices off).

    \b
    Calibration discovers (per device, per state, per intended property):
      • Rate of change (units/second)
      • Coast profile (time series after shutoff)
      • Thermal byproduct rate (if device has significant power dissipation
        and temperature is not an intended property)

    \b
    Per environment:
      • Passive conductance to ambient
      • Time constant τ = 1/g_passive

    \b
    Examples:
      spriggler calibrate run -e seedling
      spriggler calibrate run -e seedling -d heater -d humidifier
      spriggler calibrate run -e seedling --skip-passive
      spriggler calibrate run -e seedling --resume
    """
    in_development(
        command="spriggler calibrate run",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "This is a Phase 0 priority — calibration is the foundation "
            "everything else builds on.\n\n"
            "The v0.4 characterize.py has good bones: the coast detection "
            "(seen_change guard, primary-property focus, no time caps) works "
            "well.  The main changes for v0.5:\n\n"
            "  1. Convert all sensor readings to fundamental quantities at the "
            "sensor boundary BEFORE rate calculation.  This means the "
            "heater's calibrated effect on absolute humidity is zero "
            "(correct), not -0.73%RH/cycle (phantom cross-effect).\n\n"
            "  2. Calibrate each intended property independently but "
            "simultaneously — a single activation run measures rate on "
            "ALL intended properties of the device.\n\n"
            "  3. Thermal byproduct detection: if a device significantly "
            "changes temperature and temperature is NOT in its intended "
            "properties, record the temperature rate as thermal_byproduct.\n\n"
            "  4. For graduated devices (VeSync humidifier: off/low/high), "
            "run the full sequence for each non-off state independently.\n\n"
            "  5. Checkpoint/resume support — calibration takes 30+ minutes "
            "per device.  If interrupted, resume from last completed phase."
        ),
        notes=(
            "Key lesson from v0.4: the coast detection must use a "
            "'seen_change' guard on the PRIMARY intended property.  Don't "
            "start measuring coast until the sensor has reported a change "
            "in the direction you expect.  This avoids counting startup "
            "latency as coast.\n\n"
            "The sensor freshness system must be working before calibration — "
            "we gate rate calculation on FRESH sensor arrivals only.  Stale "
            "readings produce inaccurate rates.\n\n"
            "Calibration data stored as JSON in $config_dir/calibration/."
        ),
        salvage_from_v04=[
            "calibrate/characterize.py — Coast detection, rate estimation, decay measurement",
            "calibrate/power.py — Power draw measurement",
        ],
    )


@calibrate.command("device")
@click.argument("device_name")
@click.option(
    "--environment", "-e",
    required=True,
    help="Environment the device is in.",
)
@click.option(
    "--state", "-s",
    help="Calibrate a specific state only (e.g., 'low', 'high').  "
         "Default: all non-off states.",
)
@click.option(
    "--property", "-p",
    multiple=True,
    help="Calibrate specific intended property(ies) only.  "
         "Default: all intended properties declared in config.",
)
@click.pass_context
def calibrate_device(ctx, device_name, environment, state, property):
    """Calibrate a single device.

    Quick calibration of one device without running the full sequence.
    Useful for recalibrating after a drift alert or hardware change.

    \b
    Examples:
      spriggler calibrate device heater -e seedling
      spriggler calibrate device humidifier -e seedling -s high
      spriggler calibrate device heater -e seedling -p temperature
    """
    in_development(
        command="spriggler calibrate device",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Targeted single-device calibration.  Runs the same "
            "activate → measure rate → deactivate → measure coast "
            "sequence but for one device only.\n\n"
            "Primary use case: recalibration after a drift alert.  "
            "The drift detection system (EWMA) flags devices whose "
            "actual rates no longer match calibration.  This command "
            "lets you recalibrate that specific device without "
            "re-running the entire environment calibration."
        ),
        depends_on=[
            "Sensor freshness tracking",
            "Device drivers",
            "Physics plugin library",
        ],
    )


@calibrate.command("passive")
@click.option(
    "--environment", "-e",
    required=True,
    help="Environment to measure.",
)
@click.option(
    "--duration",
    type=int,
    default=None,
    help="Measurement duration (seconds).  Default: auto (3× estimated τ).",
)
@click.pass_context
def calibrate_passive(ctx, environment, duration):
    """Measure passive conductance (environment → ambient decay).

    Turns off all devices in the environment and measures how fast
    each property decays toward ambient.  Derives the passive
    conductance g_passive and time constant τ.

    \b
    This is the environment's "leakiness" — how fast it equilibrates
    with ambient when nothing is actively controlling it.  A well-
    insulated grow tent has a large τ (slow decay); an open room
    has a small τ (fast equilibration).

    \b
    The controller uses g_passive to:
      • Predict drift during coast (after device shutoff)
      • Determine if a transfer device is useful (conductance delta
        must be significant relative to passive conductance)
      • Anticipate how fast external changes (ambient shift) will
        affect the environment
    """
    in_development(
        command="spriggler calibrate passive",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Standalone passive conductance measurement.  Useful for "
            "quick characterization of a new environment without "
            "calibrating all devices.\n\n"
            "The measurement:\n"
            "  1. Ensure all devices in the environment are OFF\n"
            "  2. Record initial conditions + ambient\n"
            "  3. Wait and record decay toward ambient\n"
            "  4. Fit exponential decay to extract τ and g_passive\n"
            "  5. Store in calibration data"
        ),
        salvage_from_v04=[
            "calibrate/characterize.py — decay measurement logic",
        ],
    )


@calibrate.command("show")
@click.option(
    "--environment", "-e",
    help="Filter to a specific environment.",
)
@click.option(
    "--device", "-d",
    help="Filter to a specific device.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json", "yaml"]),
    default="table",
    help="Output format.  Default: table.",
)
@click.pass_context
def calibrate_show(ctx, environment, device, fmt):
    """Display current calibration data.

    Shows calibrated rates, coast profiles, passive conductance,
    and calibration timestamps.  Use --format json for machine-
    readable output.
    """
    in_development(
        command="spriggler calibrate show",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Reads stored calibration data and displays it in a "
            "human-readable table or machine-readable format.\n\n"
            "The table view shows:\n"
            "  Per device, per state:\n"
            "    • Rate (units/sec) for each intended property\n"
            "    • Coast overshoot (units) for each intended property\n"
            "    • Coast duration (seconds)\n"
            "    • Thermal byproduct rate (if applicable)\n"
            "    • Calibrated timestamp\n\n"
            "  Per environment:\n"
            "    • Passive conductance g_passive per property\n"
            "    • Time constant τ per property\n"
            "    • Calibrated timestamp"
        ),
    )


@calibrate.command("export")
@click.argument("output_file", type=click.Path())
@click.option(
    "--environment", "-e",
    help="Export calibration for a specific environment only.",
)
@click.pass_context
def calibrate_export(ctx, output_file, environment):
    """Export calibration data to a JSON file.

    Useful for backup, version control, or transferring calibration
    between systems with similar hardware.
    """
    in_development(
        command="spriggler calibrate export",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Serializes calibration data to a portable JSON file.  "
            "Includes all rates, coast profiles, passive conductance, "
            "and metadata (hardware identifiers, timestamps, firmware "
            "versions if available)."
        ),
    )


@calibrate.command("import")
@click.argument("input_file", type=click.Path(exists=True))
@click.option(
    "--environment", "-e",
    help="Import into a specific environment (remapping if needed).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing calibration without confirmation.",
)
@click.pass_context
def calibrate_import(ctx, input_file, environment, force):
    """Import calibration data from a JSON file.

    Validates the imported data against current config (device names
    must match, property types must match).  Warns on mismatches.
    """
    in_development(
        command="spriggler calibrate import",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Loads calibration data from a previously exported file.  "
            "Validates compatibility with the current config — device "
            "names, intended properties, and state names must match.\n\n"
            "Use case: you've calibrated on one Pi, want to transfer "
            "to another running the same hardware.  Or restoring from "
            "backup after a config corruption."
        ),
    )
