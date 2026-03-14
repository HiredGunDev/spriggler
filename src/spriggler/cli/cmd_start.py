"""spriggler start — launch the controller."""

import click


@click.command()
@click.option(
    "--foreground", "-f",
    is_flag=True,
    help="Run in foreground with live dashboard.  Ctrl-C to stop.",
)
@click.option(
    "--environment", "-e",
    help="Control only this environment.  Default: first configured.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Evaluate control decisions but don't actuate devices.",
)
@click.option(
    "--cycle-interval",
    type=float,
    default=3.0,
    help="Seconds between control cycles.  Default: 3.",
)
@click.pass_context
def start(ctx, foreground, environment, dry_run, cycle_interval):
    """Start the Spriggler controller.

    Loads config and calibration data, connects to sensors and devices,
    and begins the control loop.

    \b
    Foreground mode (-f) shows a live dashboard with environment state,
    device status, sensor health, and control messages.  Background
    mode writes status to status.json for external monitoring.

    \b
    Examples:
      spriggler start -f                 Foreground with dashboard
      spriggler start -f --dry-run       See decisions without acting
      spriggler start -f -e seedling     Control one environment
    """
    import time
    from pathlib import Path
    from rich.live import Live
    from spriggler.cli._style import console, C_BRAND, C_OK, C_WARN, C_ERROR, C_NOTE
    from spriggler.config.loader import load_config, ConfigError
    from spriggler.calibrate.engine import load_calibration
    from spriggler.controller.loop import EnvironmentController
    from spriggler.controller.dashboard import render_dashboard

    home = ctx.obj["home"]

    # Load config
    try:
        cfg = load_config(home)
    except ConfigError as e:
        console.print(f"[{C_ERROR}]Config error:[/{C_ERROR}] {e}")
        raise SystemExit(1)

    cfg["_home"] = str(home)

    # Determine environment
    envs = cfg.get("environments", {})
    if environment:
        if environment not in envs:
            console.print(
                f"[{C_ERROR}]Unknown environment: '{environment}'[/{C_ERROR}]\n"
                f"  Available: {', '.join(envs.keys())}"
            )
            raise SystemExit(1)
        env_name = environment
    else:
        # First controlled environment
        controlled = [n for n, e in envs.items()
                      if e.get("controlled", True)]
        if not controlled:
            console.print(f"[{C_ERROR}]No controlled environments in config[/{C_ERROR}]")
            raise SystemExit(1)
        env_name = controlled[0]

    # Load calibration
    cal = load_calibration(env_name, home)
    if cal is None:
        console.print(
            f"[{C_ERROR}]No calibration data for '{env_name}'[/{C_ERROR}]\n"
            f"  Run: spriggler calibrate run -e {env_name}"
        )
        raise SystemExit(1)

    if not cal.devices:
        console.print(
            f"[{C_ERROR}]Calibration has no device data[/{C_ERROR}]\n"
            f"  Run: spriggler calibrate run -e {env_name}"
        )
        raise SystemExit(1)

    # Create controller
    controller = EnvironmentController(
        cfg=cfg,
        environment=env_name,
        calibration=cal,
        dry_run=dry_run,
    )

    if dry_run:
        console.print(f"[{C_WARN}]DRY RUN — no devices will be commanded[/{C_WARN}]")

    console.print(f"[{C_BRAND} bold]Starting Spriggler — {env_name}[/{C_BRAND} bold]\n")

    # Setup
    if not controller.setup():
        console.print(f"\n[{C_ERROR}]Controller setup failed[/{C_ERROR}]")
        raise SystemExit(1)

    # Run
    previous_env = None

    if foreground:
        try:
            with Live(
                render_dashboard(controller.state, console.width),
                console=console,
                refresh_per_second=2,
                screen=True,
            ) as live:
                while True:
                    previous_env = controller.cycle(previous_env)
                    live.update(
                        render_dashboard(controller.state, console.width)
                    )
                    time.sleep(cycle_interval)
        except KeyboardInterrupt:
            console.print(f"\n[{C_NOTE}]Shutting down...[/{C_NOTE}]")
    else:
        # Background mode — just run the loop, write status.json
        console.print(f"[{C_NOTE}]Running in background mode. Ctrl-C to stop.[/{C_NOTE}]")
        try:
            while True:
                previous_env = controller.cycle(previous_env)
                # TODO: write status.json
                time.sleep(cycle_interval)
        except KeyboardInterrupt:
            console.print(f"\n[{C_NOTE}]Shutting down...[/{C_NOTE}]")

    # Cleanup — log stop
    if hasattr(controller, '_slog') and controller._slog:
        controller._slog.log_stop(controller.state.cycle_count)
        controller._slog.close()

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

    console.print(f"[{C_OK}]Spriggler stopped[/{C_OK}]")
