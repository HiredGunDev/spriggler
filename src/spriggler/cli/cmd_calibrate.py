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

    All measurements use fundamental physical quantities — temperature
    in Kelvin, moisture as absolute humidity (g/m³).  No phantom
    cross-effects possible.

    \b
    Subcommands:
      spriggler calibrate run       Run calibration sequence
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
@click.pass_context
def calibrate_run(ctx, environment, device, include_passive):
    """Run calibration sequence for an environment.

    For each device in the environment, measures:
      • Rate of change on intended properties (fundamental units)
      • Coast profile after shutoff
      • Thermal byproduct (if device has significant power draw
        and temperature is not an intended property)

    For graduated devices (e.g., humidifier off/low/high), each
    non-off state is calibrated independently.

    After devices: measures passive conductance (all devices off,
    environment decays toward ambient).

    \b
    Examples:
      spriggler calibrate run -e seedling
      spriggler calibrate run -e seedling -d seedling_heater
      spriggler calibrate run -e seedling -d seedling_heater -d seedling_humidifier
      spriggler calibrate run -e seedling --skip-passive
    """
    import time
    from rich.live import Live
    from rich.text import Text
    from rich.panel import Panel
    from rich import box
    from spriggler.cli._style import (
        console, C_BRAND, C_OK, C_WARN, C_ERROR, C_NOTE, C_CMD,
    )
    from spriggler.config.loader import load_config, ConfigError
    from spriggler.calibrate.engine import (
        CalibrationEngine, save_calibration,
    )
    from spriggler.physics.temperature import kelvin_to_fahrenheit

    # Load config
    home = ctx.obj["home"]
    try:
        cfg = load_config(home)
    except ConfigError as e:
        console.print(f"[{C_ERROR}]Config error:[/{C_ERROR}] {e}")
        raise SystemExit(1)

    # Validate environment exists
    if environment not in cfg.get("environments", {}):
        available = ", ".join(cfg.get("environments", {}).keys())
        console.print(
            f"[{C_ERROR}]Unknown environment: '{environment}'[/{C_ERROR}]\n"
            f"  Available: {available}"
        )
        raise SystemExit(1)

    # Status log for display
    status_lines: list[str] = []
    max_status_lines = 30

    def on_status(msg: str):
        status_lines.append(msg)
        if len(status_lines) > max_status_lines:
            status_lines.pop(0)
        console.print(f"[{C_NOTE}]{msg}[/{C_NOTE}]")

    # Create engine
    engine = CalibrationEngine(
        cfg=cfg,
        environment=environment,
        on_status=on_status,
    )

    # Setup
    console.print(f"\n[{C_BRAND} bold]Calibrating environment: {environment}[/{C_BRAND} bold]\n")

    if not engine.setup():
        console.print(f"\n[{C_ERROR}]Calibration setup failed — check sensor connectivity[/{C_ERROR}]")
        raise SystemExit(1)

    # Determine device targets
    device_names = list(device) if device else None

    # Run calibration
    try:
        env_cal = engine.run(
            device_names=device_names,
            include_passive=include_passive,
        )
    except KeyboardInterrupt:
        console.print(f"\n[{C_WARN}]Calibration interrupted by user[/{C_WARN}]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"\n[{C_ERROR}]Calibration error: {e}[/{C_ERROR}]")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)

    # Save results
    filepath = save_calibration(env_cal, home)
    console.print(f"\n[{C_OK}]Calibration saved: {filepath}[/{C_OK}]")

    # Summary
    console.print(f"\n[{C_BRAND} bold]Calibration Summary[/{C_BRAND} bold]")

    for dev_name, dcal in env_cal.devices.items():
        console.print(f"\n  [{C_CMD}]{dev_name}[/{C_CMD}]")
        for state, scal in dcal.states.items():
            console.print(f"    State: {state}")
            if scal.power_draw is not None:
                console.print(f"      Power: {scal.power_draw:.1f}W")
            for prop, pcal in scal.properties.items():
                rate_display = pcal.rate
                unit = ""
                if prop == "temperature":
                    # Convert K/s to °F/min for readability
                    rate_display = pcal.rate * 9/5 * 60  # K/s → °F/min
                    coast_display = pcal.coast_overshoot * 9/5  # K → °F
                    unit = "°F"
                    console.print(
                        f"      {prop}: rate={rate_display:+.3f}°F/min  "
                        f"coast={coast_display:+.3f}°F over {pcal.coast_duration:.0f}s"
                    )
                elif prop == "absolute_humidity":
                    rate_display = pcal.rate * 60  # g/m³/s → g/m³/min
                    console.print(
                        f"      {prop}: rate={rate_display:+.4f} g/m³/min  "
                        f"coast={pcal.coast_overshoot:+.4f} g/m³ over {pcal.coast_duration:.0f}s"
                    )
                else:
                    console.print(
                        f"      {prop}: rate={pcal.rate:.6f}/s  "
                        f"coast={pcal.coast_overshoot:+.4f}"
                    )

            if scal.thermal_byproduct_rate is not None:
                byproduct_fpm = scal.thermal_byproduct_rate * 9/5 * 60
                console.print(
                    f"      thermal byproduct: {byproduct_fpm:+.3f}°F/min"
                )

    if env_cal.passive_conductance:
        console.print(f"\n  [{C_CMD}]Passive Conductance[/{C_CMD}]")
        for prop, g in env_cal.passive_conductance.items():
            tau = env_cal.time_constant.get(prop, 0)
            console.print(
                f"    {prop}: g={g:.6f}/s  τ={tau:.0f}s ({tau/60:.1f}min)"
            )

    # Clean shutdown
    try:
        from spriggler.sensors.govee import GoveeSensor
        GoveeSensor.stop_scanner()
    except Exception:
        pass
    try:
        from spriggler.devices.kasa_mgr import shutdown_kasa_manager
        shutdown_kasa_manager()
    except Exception:
        pass
    try:
        from spriggler.devices.vesync_mgr import shutdown_vesync_manager
        shutdown_vesync_manager()
    except Exception:
        pass


@calibrate.command("show")
@click.option(
    "--environment", "-e",
    help="Filter to a specific environment.",
)
@click.option(
    "--device", "-d",
    help="Filter to a specific device.",
)
@click.pass_context
def calibrate_show(ctx, environment, device):
    """Display current calibration data.

    Shows calibrated rates, coast profiles, passive conductance,
    and calibration timestamps.
    """
    from rich.table import Table
    from rich import box
    from spriggler.cli._style import (
        console, C_BRAND, C_CMD, C_NOTE, C_WARN,
    )
    from spriggler.calibrate.engine import load_calibration
    from spriggler.config.loader import load_config, ConfigError

    home = ctx.obj["home"]

    # Find calibration files
    cal_dir = home / "calibration"
    if not cal_dir.is_dir():
        console.print(f"[{C_WARN}]No calibration data found at {cal_dir}[/{C_WARN}]")
        console.print(f"  Run: spriggler calibrate run -e <environment>")
        return

    # List available calibrations
    cal_files = sorted(cal_dir.glob("*.json"))
    if not cal_files:
        console.print(f"[{C_WARN}]No calibration data found[/{C_WARN}]")
        console.print(f"  Run: spriggler calibrate run -e <environment>")
        return

    for cal_file in cal_files:
        env_name = cal_file.stem
        if environment and env_name != environment:
            continue

        cal = load_calibration(env_name, home)
        if cal is None:
            continue

        import datetime
        cal_time = datetime.datetime.fromtimestamp(cal.calibrated_at)

        console.print(
            f"\n[{C_BRAND} bold]Calibration: {env_name}[/{C_BRAND} bold]"
            f"  [{C_NOTE}](calibrated {cal_time:%Y-%m-%d %H:%M})[/{C_NOTE}]"
        )

        # Device calibrations
        for dev_name, dcal in cal.devices.items():
            if device and dev_name != device:
                continue

            t = Table(
                title=dev_name,
                box=box.ROUNDED,
                title_style=f"bold {C_CMD}",
                header_style="bold",
            )
            t.add_column("State")
            t.add_column("Property")
            t.add_column("Rate", justify="right")
            t.add_column("Coast", justify="right")
            t.add_column("Coast Time", justify="right")
            t.add_column("Power", justify="right")
            t.add_column("Byproduct", justify="right")

            for state, scal in dcal.states.items():
                for prop, pcal in scal.properties.items():
                    if prop == "temperature":
                        rate_str = f"{pcal.rate * 9/5 * 60:+.3f}°F/min"
                        coast_str = f"{pcal.coast_overshoot * 9/5:+.3f}°F"
                    elif prop == "absolute_humidity":
                        rate_str = f"{pcal.rate * 60:+.4f} g/m³/min"
                        coast_str = f"{pcal.coast_overshoot:+.4f} g/m³"
                    else:
                        rate_str = f"{pcal.rate:.6f}/s"
                        coast_str = f"{pcal.coast_overshoot:+.4f}"

                    power_str = f"{scal.power_draw:.0f}W" if scal.power_draw else "—"
                    byproduct_str = (
                        f"{scal.thermal_byproduct_rate * 9/5 * 60:+.3f}°F/min"
                        if scal.thermal_byproduct_rate else "—"
                    )

                    t.add_row(
                        state, prop, rate_str, coast_str,
                        f"{pcal.coast_duration:.0f}s",
                        power_str, byproduct_str,
                    )

            console.print(t)

        # Passive conductance
        if cal.passive_conductance:
            pt = Table(
                title="Passive Conductance",
                box=box.ROUNDED,
                title_style=f"bold {C_CMD}",
                header_style="bold",
            )
            pt.add_column("Property")
            pt.add_column("g_passive", justify="right")
            pt.add_column("τ (time constant)", justify="right")

            for prop, g in cal.passive_conductance.items():
                tau = cal.time_constant.get(prop, 0)
                pt.add_row(
                    prop,
                    f"{g:.6f}/s",
                    f"{tau:.0f}s ({tau/60:.1f}min)",
                )

            console.print(pt)


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
            "and metadata."
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
            "Validates compatibility with current config."
        ),
    )
