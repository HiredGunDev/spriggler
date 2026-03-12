"""spriggler device — discover, control, and monitor devices."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def device(ctx):
    """Discover, control, and monitor devices.

    Devices are the system's hands.  These commands let you find
    available devices, send commands, read power, and inspect state.

    \b
    Subcommands:
      spriggler device scan       Discover KASA devices on the network
      spriggler device set        Set a device to a specific state
      spriggler device power      Read power consumption
      spriggler device info       Show device details
      spriggler device test       Cycle a device through all states
    """
    pass


def _init_kasa():
    """Start the KASA connection manager and discover devices."""
    try:
        from spriggler.devices.kasa_mgr import get_kasa_manager
        return get_kasa_manager()
    except ImportError:
        from spriggler.cli._style import console
        console.print(
            "[bold red]python-kasa not installed.[/bold red]\n"
            "  Run: pip install spriggler[kasa]"
        )
        raise SystemExit(1)


def _load_config(ctx):
    """Load config, return (cfg, None) or (None, error_msg)."""
    from spriggler.config.loader import load_config, ConfigError
    try:
        return load_config(ctx.obj["home"]), None
    except ConfigError as e:
        return None, str(e)


def _get_vesync_credentials(cfg) -> dict | None:
    """Extract VeSync credentials from config if available."""
    if cfg is None:
        return None
    for dev in cfg.get("devices", {}).values():
        if dev.get("driver") == "vesync_humidifier":
            dc = dev.get("driver_config", {})
            email = dc.get("email")
            password = dc.get("password")
            if email and password:
                return {"email": email, "password": password}
    return None


@device.command("scan")
@click.option(
    "--timeout", "-t",
    type=int,
    default=20,
    help="Discovery timeout in seconds.  Default: 20.",
)
@click.pass_context
def device_scan(ctx, timeout):
    """Discover KASA devices on the local network.

    Shows all KASA smart plugs and strips with their aliases,
    models, plug names, and current state.

    \b
    Examples:
      spriggler device scan
      spriggler device scan -t 20    Longer timeout for slow networks
    """
    from rich.table import Table
    from rich import box
    from spriggler.cli._style import console, C_BRAND, C_CMD, C_NOTE, C_OK, C_WARN

    # Load config for cross-reference
    cfg, _ = _load_config(ctx)
    configured_plugs = set()
    if cfg:
        for dev in cfg.get("devices", {}).values():
            dc = dev.get("driver_config", {})
            strip = dc.get("strip", "")
            plug = dc.get("plug", "")
            if strip and plug:
                configured_plugs.add((strip, plug))

    console.print(f"[{C_NOTE}]Discovering KASA devices (timeout {timeout}s)...[/{C_NOTE}]")

    from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError
    try:
        mgr = get_kasa_manager(discovery_timeout=timeout)
        mgr.discover()
    except KasaError as e:
        console.print(f"[bold red]Discovery failed:[/bold red] {e}")
        raise SystemExit(1)

    devices = mgr.list_devices()

    if not devices:
        console.print(f"[{C_WARN}]No KASA devices found on the network.[/{C_WARN}]")
        return

    t = Table(
        title="KASA Devices",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Device", style=C_CMD)
    t.add_column("Model")
    t.add_column("Plug", style=C_CMD)
    t.add_column("State")
    t.add_column("Power", justify="right")
    t.add_column("Configured")
    t.add_column("Host")

    for dev_info in devices:
        alias = dev_info["alias"]
        model = dev_info["model"]
        host = dev_info["host"]

        if dev_info["is_strip"]:
            # Show each child plug as a row
            for child_name in dev_info["children"]:
                try:
                    plug = mgr.get_plug(alias, child_name)
                    mgr.update_device(plug)
                    state = "on" if mgr.is_on(plug) else "off"
                    state_style = C_OK if state == "on" else C_NOTE
                    power = mgr.read_power(plug)
                    power_str = f"{power:.1f}W" if power is not None else "—"

                    is_cfg = (alias, child_name) in configured_plugs
                    cfg_str = f"[{C_OK}]yes[/{C_OK}]" if is_cfg else f"[{C_NOTE}]no[/{C_NOTE}]"

                    t.add_row(
                        alias, model, child_name,
                        f"[{state_style}]{state}[/{state_style}]",
                        power_str, cfg_str, host,
                    )
                except Exception:
                    t.add_row(alias, model, child_name, "?", "—", "—", host)
        else:
            # Standalone plug
            try:
                plug = mgr.get_plug(alias, alias)
                mgr.update_device(plug)
                state = "on" if mgr.is_on(plug) else "off"
                state_style = C_OK if state == "on" else C_NOTE
                power = mgr.read_power(plug)
                power_str = f"{power:.1f}W" if power is not None else "—"

                is_cfg = (alias, alias) in configured_plugs
                cfg_str = f"[{C_OK}]yes[/{C_OK}]" if is_cfg else f"[{C_NOTE}]no[/{C_NOTE}]"

                t.add_row(
                    alias, model, "—",
                    f"[{state_style}]{state}[/{state_style}]",
                    power_str, cfg_str, host,
                )
            except Exception:
                t.add_row(alias, model, "—", "?", "—", "—", host)

    console.print(t)

    # ── VeSync discovery ─────────────────────────────────────
    # Try to discover VeSync devices if credentials are available
    cfg, _ = _load_config(ctx)
    vesync_creds = _get_vesync_credentials(cfg)
    if vesync_creds:
        try:
            from spriggler.devices.vesync_mgr import get_vesync_manager
            vmgr = get_vesync_manager(**vesync_creds)
            humidifiers = vmgr.list_humidifiers()

            if humidifiers:
                vt = Table(
                    title="VeSync Devices",
                    box=box.ROUNDED,
                    title_style=f"bold {C_BRAND}",
                    header_style="bold",
                )
                vt.add_column("Name", style=C_CMD)
                vt.add_column("Type")
                vt.add_column("State")
                vt.add_column("Configured")

                # Check which VeSync devices are configured
                configured_vesync = set()
                if cfg:
                    for dev in cfg.get("devices", {}).values():
                        if dev.get("driver") == "vesync_humidifier":
                            dc = dev.get("driver_config", {})
                            vname = dc.get("name", "")
                            if vname:
                                configured_vesync.add(vname)

                for h in humidifiers:
                    state = "on" if h["is_on"] else "off"
                    state_style = C_OK if state == "on" else C_NOTE
                    is_cfg = h["name"] in configured_vesync
                    cfg_str = f"[{C_OK}]yes[/{C_OK}]" if is_cfg else f"[{C_NOTE}]no[/{C_NOTE}]"
                    vt.add_row(
                        h["name"], h["type"],
                        f"[{state_style}]{state}[/{state_style}]",
                        cfg_str,
                    )
                console.print(vt)

            from spriggler.devices.vesync_mgr import shutdown_vesync_manager
            shutdown_vesync_manager()
        except Exception as e:
            console.print(f"[{C_NOTE}]VeSync discovery: {e}[/{C_NOTE}]")

    from spriggler.devices.kasa_mgr import shutdown_kasa_manager
    shutdown_kasa_manager()


@device.command("set")
@click.argument("device_name")
@click.argument("state")
@click.pass_context
def device_set(ctx, device_name, state):
    """Set a device to a specific state.

    \b
    States depend on device type:
      Binary (KASA plugs):   on, off
      Graduated (VeSync):    off, low, high

    \b
    Examples:
      spriggler device set seedling_heater on
      spriggler device set seedling_fan off
      spriggler device set seedling_humidifier low
    """
    from spriggler.cli._style import console, C_OK, C_WARN, C_ERROR, C_NOTE
    from spriggler.config.loader import load_config, ConfigError
    from spriggler.util.discovery import discover_plugins

    home = ctx.obj["home"]
    try:
        cfg = load_config(home)
    except ConfigError as e:
        console.print(f"[{C_ERROR}]Config error:[/{C_ERROR}] {e}")
        raise SystemExit(1)

    devices_cfg = cfg.get("devices", {})
    if device_name not in devices_cfg:
        available = ", ".join(devices_cfg.keys()) or "none"
        console.print(
            f"[{C_ERROR}]Unknown device: '{device_name}'[/{C_ERROR}]\n"
            f"  Available: {available}"
        )
        raise SystemExit(1)

    dev_cfg = devices_cfg[device_name]
    driver_name = dev_cfg.get("driver")
    driver_config = dev_cfg.get("driver_config", {})

    # Discover device drivers
    discover_plugins(package="spriggler.devices", exclude={"kasa_mgr", "vesync_mgr"})

    from spriggler.devices import driver_registry
    driver_cls = driver_registry.get(driver_name)
    if driver_cls is None:
        console.print(
            f"[{C_ERROR}]Unknown driver: '{driver_name}'[/{C_ERROR}]\n"
            f"  Available: {', '.join(driver_registry.list_drivers().keys()) or 'none'}"
        )
        raise SystemExit(1)

    try:
        dev = driver_cls(device_name=device_name, driver_config=driver_config)
    except Exception as e:
        console.print(f"[{C_ERROR}]Failed to init device:[/{C_ERROR}] {e}")
        raise SystemExit(1)

    available_states = dev.get_states()
    if state not in available_states:
        console.print(
            f"[{C_ERROR}]Invalid state '{state}' for {device_name}[/{C_ERROR}]\n"
            f"  Available: {', '.join(available_states)}"
        )
        raise SystemExit(1)

    success = dev.set_state(state)
    if success:
        console.print(f"[{C_OK}]{device_name} → {state}[/{C_OK}]")
    else:
        console.print(f"[{C_ERROR}]{device_name} → {state} FAILED[/{C_ERROR}]")
        raise SystemExit(1)

    # Clean shutdown
    try:
        from spriggler.devices.kasa_mgr import shutdown_kasa_manager
        shutdown_kasa_manager()
    except Exception:
        pass


@device.command("power")
@click.argument("device_name", required=False)
@click.option(
    "--watch", "-w",
    is_flag=True,
    help="Continuously monitor power draw.  Ctrl-C to stop.",
)
@click.pass_context
def device_power(ctx, device_name, watch):
    """Read power consumption from devices.

    Without a device name, shows power for all KASA-monitored devices.
    With --watch, continuously updates until Ctrl-C.

    \b
    Examples:
      spriggler device power                  All monitored devices
      spriggler device power seedling_heater  Single device
      spriggler device power --watch          Live monitoring
    """
    import time
    from rich.live import Live
    from rich.table import Table
    from rich import box
    from spriggler.cli._style import console, C_BRAND, C_CMD, C_NOTE, C_OK, C_WARN
    from spriggler.config.loader import load_config, ConfigError

    home = ctx.obj["home"]
    try:
        cfg = load_config(home)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    # Build device → plug mapping from power_monitoring config
    pm = cfg.get("power_monitoring", {})
    plug_to_device: dict[tuple[str, str], str] = {}
    for strip_name, strip_data in pm.items():
        for plug_name, dev_name in strip_data.get("plug_map", {}).items():
            plug_to_device[(strip_data.get("strip", strip_name), plug_name)] = dev_name

    # If a specific device was requested, filter
    if device_name:
        plug_to_device = {
            k: v for k, v in plug_to_device.items() if v == device_name
        }
        if not plug_to_device:
            console.print(
                f"[bold red]No power monitoring configured for '{device_name}'[/bold red]"
            )
            raise SystemExit(1)

    from spriggler.devices.kasa_mgr import get_kasa_manager, KasaError

    try:
        mgr = get_kasa_manager()
    except Exception as e:
        console.print(f"[bold red]KASA init failed:[/bold red] {e}")
        raise SystemExit(1)

    def build_table() -> Table:
        t = Table(
            title="Power Monitoring",
            box=box.ROUNDED,
            title_style=f"bold {C_BRAND}",
            header_style="bold",
        )
        t.add_column("Device", style=C_CMD)
        t.add_column("Strip / Plug")
        t.add_column("State")
        t.add_column("Power", justify="right")

        for (strip, plug_name), dev_name in sorted(plug_to_device.items()):
            try:
                plug = mgr.get_plug(strip, plug_name)
                mgr.update_device(plug)
                state = "on" if mgr.is_on(plug) else "off"
                state_style = C_OK if state == "on" else C_NOTE
                power = mgr.read_power(plug)
                power_str = f"{power:.1f}W" if power is not None else "—"
            except KasaError:
                state = "?"
                state_style = C_WARN
                power_str = "—"

            t.add_row(
                dev_name,
                f"{strip} / {plug_name}",
                f"[{state_style}]{state}[/{state_style}]",
                power_str,
            )

        return t

    if watch:
        console.print(f"[{C_NOTE}]Monitoring power... Ctrl-C to stop[/{C_NOTE}]\n")
        try:
            with Live(build_table(), console=console, refresh_per_second=1) as live:
                while True:
                    time.sleep(1)
                    live.update(build_table())
        except KeyboardInterrupt:
            pass
    else:
        console.print(build_table())

    from spriggler.devices.kasa_mgr import shutdown_kasa_manager
    shutdown_kasa_manager()


@device.command("info")
@click.argument("device_name")
@click.pass_context
def device_info(ctx, device_name):
    """Show device details and capabilities.

    \b
    Displays:
      • Device type, driver, connection method
      • Available states
      • Intended properties and directions (from config)
      • Current hardware state and power draw
      • Countdown timer support
    """
    from spriggler.cli._style import console, C_BRAND, C_CMD, C_NOTE, C_OK, C_WARN, C_ERROR
    from spriggler.config.loader import load_config, ConfigError
    from spriggler.util.discovery import discover_plugins
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    home = ctx.obj["home"]
    try:
        cfg = load_config(home)
    except ConfigError as e:
        console.print(f"[{C_ERROR}]Config error:[/{C_ERROR}] {e}")
        raise SystemExit(1)

    devices_cfg = cfg.get("devices", {})
    if device_name not in devices_cfg:
        available = ", ".join(devices_cfg.keys()) or "none"
        console.print(
            f"[{C_ERROR}]Unknown device: '{device_name}'[/{C_ERROR}]\n"
            f"  Available: {available}"
        )
        raise SystemExit(1)

    dev_cfg = devices_cfg[device_name]
    driver_name = dev_cfg.get("driver")
    driver_config = dev_cfg.get("driver_config", {})

    discover_plugins(package="spriggler.devices", exclude={"kasa_mgr", "vesync_mgr"})

    from spriggler.devices import driver_registry
    driver_cls = driver_registry.get(driver_name)

    body = Text()
    body.append("Name:        ", style="bold")
    body.append(f"{device_name}\n", style=C_CMD)
    body.append("Driver:      ", style="bold")
    body.append(f"{driver_name}\n")
    body.append("Type:        ", style="bold")
    body.append(f"{dev_cfg.get('type', '?')}\n")
    body.append("Environment: ", style="bold")
    body.append(f"{dev_cfg.get('environment', '?')}\n")
    body.append("Circuit:     ", style="bold")
    body.append(f"{dev_cfg.get('circuit', '—')}\n")

    # Intended properties
    props = dev_cfg.get("intended_properties", {})
    body.append("Properties:  ", style="bold")
    if props:
        body.append(", ".join(f"{p} ({d})" for p, d in props.items()))
    elif dev_cfg.get("scheduled"):
        body.append("scheduled (no climate properties)", style=C_NOTE)
    else:
        body.append("none (transfer device)", style=C_NOTE)
    body.append("\n")

    # Try to instantiate and query hardware
    if driver_cls is not None:
        try:
            dev = driver_cls(device_name=device_name, driver_config=driver_config)
            body.append("States:      ", style="bold")
            body.append(" → ".join(dev.get_states()))
            body.append("\n")

            if hasattr(dev, "query_hardware_state"):
                hw_state = dev.query_hardware_state()
                state_style = C_OK if hw_state != "off" else C_NOTE
                body.append("HW State:    ", style="bold")
                body.append(hw_state, style=state_style)
                body.append("\n")

            if hasattr(dev, "read_power"):
                power = dev.read_power()
                body.append("Power:       ", style="bold")
                if power is not None:
                    body.append(f"{power:.1f}W")
                else:
                    body.append("—", style=C_NOTE)
                body.append("\n")

            body.append("Countdown:   ", style="bold")
            body.append("yes" if dev.supports_countdown() else "no")
            body.append("\n")

        except Exception as e:
            body.append(f"\nHardware:    ", style="bold")
            body.append(f"unreachable ({e})", style=C_WARN)
            body.append("\n")
    else:
        body.append(f"\nDriver:      ", style="bold")
        body.append(f"'{driver_name}' not loaded", style=C_WARN)
        body.append("\n")

    console.print(Panel(
        body,
        title=f"[{C_BRAND}]{device_name}[/{C_BRAND}]",
        border_style=C_BRAND,
        box=box.ROUNDED,
        padding=(1, 2),
    ))

    try:
        from spriggler.devices.kasa_mgr import shutdown_kasa_manager
        shutdown_kasa_manager()
    except Exception:
        pass


@device.command("test")
@click.argument("device_name")
@click.option(
    "--cycle",
    is_flag=True,
    help="Cycle through all device states with pauses for confirmation.",
)
@click.pass_context
def device_test(ctx, device_name, cycle):
    """Verify device connectivity and control.

    Tests that commands reach the device.  With --cycle, steps through
    all available states.

    \b
    Examples:
      spriggler device test seedling_heater
      spriggler device test seedling_humidifier --cycle
    """
    in_development(
        command="spriggler device test",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Device connectivity and control verification.  The --cycle "
            "test is especially useful for graduated devices (humidifier "
            "off → low → high → off) to verify all states are reachable."
        ),
        depends_on=[
            "Device drivers (KASA, VeSync)",
        ],
    )
