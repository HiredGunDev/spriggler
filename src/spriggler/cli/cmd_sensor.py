"""spriggler sensor — discover, test, and monitor sensors."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def sensor(ctx):
    """Discover, test, and monitor sensors.

    Sensors are the system's eyes.  These commands help you find
    available sensors, verify they're working, and monitor their
    behavior independently of the controller.

    \b
    Subcommands:
      spriggler sensor scan       Scan for available BLE sensors
      spriggler sensor watch      Live-stream readings from a sensor
      spriggler sensor test       Verify sensor connectivity and data quality
      spriggler sensor history    Show recent reading history for a sensor
    """
    pass


@sensor.command("scan")
@click.option(
    "--duration", "-d",
    type=int,
    default=30,
    help="Scan duration in seconds.  Default: 30.",
)
@click.option(
    "--continuous", "-c",
    is_flag=True,
    help="Scan until Ctrl-C (ignores --duration).",
)
@click.pass_context
def sensor_scan(ctx, duration, continuous):
    """Scan for BLE sensors.

    Discovers Govee and other BLE environmental sensors by listening
    for advertisements.  Shows device name, address/suffix, signal
    strength, and live readings if available.

    \b
    Examples:
      spriggler sensor scan             Scan for 30 seconds
      spriggler sensor scan -d 60       Scan for 60 seconds
      spriggler sensor scan -c          Scan until Ctrl-C
    """
    import asyncio
    import time
    from rich.live import Live
    from rich.table import Table
    from rich import box
    from spriggler.cli._style import console, C_BRAND, C_CMD, C_NOTE, C_OK, C_WARN

    # Late import — BLE deps are optional
    try:
        from bleak import BleakScanner
    except ImportError:
        console.print(
            "[bold red]bleak not installed.[/bold red]\n"
            "  Run: pip install spriggler[ble]"
        )
        raise SystemExit(1)

    # Discovered devices: address → {name, rssi, readings, last_seen}
    discovered: dict[str, dict] = {}

    # Load config to cross-reference configured sensors
    from spriggler.config.loader import load_config
    home = ctx.obj["home"]
    configured_addresses = set()
    try:
        cfg = load_config(home)
        for s in cfg.get("sensors", {}).values():
            dc = s.get("driver_config", {})
            addr = dc.get("address", "").upper()
            if addr:
                configured_addresses.add(addr)
    except Exception:
        pass  # Config not available — that's fine for scan

    # Gateway models to filter out
    _SCAN_GATEWAY_MODELS = {"H5151"}

    def on_detection(device, advertisement_data):
        local_name = str(
            advertisement_data.local_name or device.name or ""
        )
        address = str(device.address)
        rssi = int(advertisement_data.rssi)

        # Only show Govee-like sensors (name starts with GVH or Govee)
        upper_name = local_name.upper()
        is_govee = (
            upper_name.startswith("GVH")
            or upper_name.startswith("GOVEE")
        )
        if not is_govee:
            return

        # Filter out gateways/hubs
        if any(model in upper_name for model in _SCAN_GATEWAY_MODELS):
            return

        # Extract suffix from name (e.g., "GVH5100_2650" → "2650")
        suffix = ""
        if "_" in local_name:
            suffix = local_name.split("_", 1)[1]

        # Try to decode temperature/humidity from manufacturer data
        temp_c = None
        rh = None
        try:
            for mfr_id, mfr_data in advertisement_data.manufacturer_data.items():
                if len(mfr_data) >= 6:
                    # Govee encodes temp+humidity in 3 bytes
                    # This is the raw decode before govee-ble parsing
                    raw = (mfr_data[1] << 16) | (mfr_data[2] << 8) | mfr_data[3]
                    if raw > 0:
                        is_negative = bool(raw & 0x800000)
                        raw_val = raw & 0x7FFFFF
                        temp_c = raw_val / 10000.0
                        if is_negative:
                            temp_c = -temp_c
                        rh = (raw_val % 1000) / 10.0
        except Exception:
            pass

        discovered[address] = {
            "name": local_name,
            "suffix": suffix,
            "rssi": rssi,
            "temp_c": temp_c,
            "rh": rh,
            "last_seen": time.time(),
        }

    def build_table(elapsed: int) -> Table:
        if continuous:
            title = f"BLE Sensor Scan ({elapsed}s — Ctrl-C to stop)"
        else:
            title = f"BLE Sensor Scan ({elapsed}s / {duration}s)"
        t = Table(
            title=title,
            box=box.ROUNDED,
            title_style=f"bold {C_BRAND}",
            header_style="bold",
        )
        t.add_column("Name", style=C_CMD)
        t.add_column("Suffix")
        t.add_column("RSSI", justify="right")
        t.add_column("Temp", justify="right")
        t.add_column("RH", justify="right")
        t.add_column("Configured")

        for addr, info in sorted(discovered.items(), key=lambda x: x[1]["rssi"], reverse=True):
            rssi = info["rssi"]
            rssi_style = C_OK if rssi > -70 else (C_WARN if rssi > -85 else "red")

            temp_c = info["temp_c"]
            if temp_c is not None:
                temp_f = temp_c * 9.0 / 5.0 + 32.0
                temp_str = f"{temp_f:.1f}°F ({temp_c:.1f}°C)"
            else:
                temp_str = "—"
            rh_str = f"{info['rh']:.1f}%" if info["rh"] is not None else "—"

            suffix = info["suffix"]
            is_configured = suffix.upper() in configured_addresses
            cfg_str = f"[{C_OK}]yes[/{C_OK}]" if is_configured else f"[{C_NOTE}]no[/{C_NOTE}]"

            t.add_row(
                info["name"],
                suffix,
                f"[{rssi_style}]{rssi} dBm[/{rssi_style}]",
                temp_str,
                rh_str,
                cfg_str,
            )

        if not discovered:
            t.add_row(
                f"[{C_NOTE}]Listening...[/{C_NOTE}]",
                "", "", "", "", "",
            )

        return t

    async def run_scan():
        scanner = BleakScanner(
            detection_callback=on_detection,
            scanning_mode="active",
        )
        start = time.time()

        with Live(build_table(0), console=console, refresh_per_second=2) as live:
            async with scanner:
                try:
                    while continuous or (time.time() - start < duration):
                        await asyncio.sleep(0.5)
                        elapsed = int(time.time() - start)
                        live.update(build_table(elapsed))
                except asyncio.CancelledError:
                    pass

    try:
        asyncio.run(run_scan())
    except KeyboardInterrupt:
        pass

    console.print(f"\n  [{C_NOTE}]{len(discovered)} sensor(s) found[/{C_NOTE}]")


@sensor.command("watch")
@click.argument("sensor_name")
@click.option(
    "--duration", "-d",
    type=int,
    default=None,
    help="Watch duration in seconds.  Default: until Ctrl-C.",
)
@click.option(
    "--raw",
    is_flag=True,
    help="Show raw values (°C, %RH) alongside SI fundamentals.",
)
@click.pass_context
def sensor_watch(ctx, sensor_name, duration, raw):
    """Live-stream readings from a configured sensor.

    Connects to the sensor defined in config and shows readings as
    they arrive, with freshness classification and delivery timing.

    \b
    The display updates in-place and shows:
      • Current reading (Kelvin, g/m³ — and °F, %RH with --raw)
      • Sample timestamp and age
      • Freshness classification
      • Delivery interval (actual vs expected)
      • Battery and signal strength

    \b
    Examples:
      spriggler sensor watch seedling_sensor
      spriggler sensor watch ambient_sensor --raw
      spriggler sensor watch seedling_sensor -d 300
    """
    import time
    from rich.live import Live
    from rich.table import Table
    from rich import box
    from spriggler.cli._style import console, C_BRAND, C_CMD, C_NOTE, C_OK, C_WARN, C_STALE, C_DEAD
    from spriggler.config.loader import load_config, ConfigError
    from spriggler.util.discovery import discover_plugins
    from spriggler.sensors.freshness import classify_freshness, Freshness

    # Load config
    home = ctx.obj["home"]
    try:
        cfg = load_config(home)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    # Find the sensor in config
    sensors_cfg = cfg.get("sensors", {})
    if sensor_name not in sensors_cfg:
        available = ", ".join(sensors_cfg.keys()) or "none"
        console.print(
            f"[bold red]Unknown sensor: '{sensor_name}'[/bold red]\n"
            f"  Available: {available}"
        )
        raise SystemExit(1)

    sensor_cfg = sensors_cfg[sensor_name]
    driver_name = sensor_cfg.get("driver")
    driver_config = sensor_cfg.get("driver_config", {})
    delivery_interval = sensor_cfg.get("delivery_interval_seconds", 10)

    # Freshness multipliers (per-sensor override or global defaults)
    defaults = cfg.get("sensor_defaults", {})
    fresh_mult = sensor_cfg.get("fresh_multiplier", defaults.get("fresh_multiplier", 1.5))
    aging_mult = sensor_cfg.get("aging_multiplier", defaults.get("aging_multiplier", 3.0))
    dead_mult = sensor_cfg.get("dead_multiplier", defaults.get("dead_multiplier", 10.0))

    # Discover physics plugins (needed for %RH → g/m³ in driver)
    discover_plugins(package="spriggler.physics")

    # Discover and instantiate sensor driver
    discover_plugins(package="spriggler.sensors", exclude={"base", "freshness"})

    from spriggler.sensors import driver_registry
    driver_cls = driver_registry.get(driver_name)
    if driver_cls is None:
        console.print(
            f"[bold red]Unknown driver: '{driver_name}'[/bold red]\n"
            f"  Available: {', '.join(driver_registry.list_drivers().keys()) or 'none'}"
        )
        raise SystemExit(1)

    try:
        sensor_inst = driver_cls(sensor_name=sensor_name, driver_config=driver_config)
    except Exception as e:
        console.print(f"[bold red]Failed to initialize sensor:[/bold red] {e}")
        raise SystemExit(1)

    # For display: reverse conversion g/m³ → %RH
    from spriggler.physics import registry as physics_registry
    from spriggler.physics.temperature import kelvin_to_fahrenheit

    # Track delivery stats
    reading_count = 0
    last_sample_time = None
    intervals: list[float] = []

    FRESHNESS_STYLES = {
        Freshness.FRESH: C_OK,
        Freshness.AGING: C_WARN,
        Freshness.STALE: C_STALE,
        Freshness.DEAD: C_DEAD,
    }

    def build_display() -> Table:
        nonlocal reading_count, last_sample_time

        reading = sensor_inst.read()
        freshness = classify_freshness(
            reading, delivery_interval,
            fresh_mult, aging_mult, dead_mult,
        )

        t = Table(
            title=f"Sensor: {sensor_name}",
            box=box.ROUNDED,
            title_style=f"bold {C_BRAND}",
            show_header=False,
            padding=(0, 2),
        )
        t.add_column("Key", style="bold")
        t.add_column("Value")

        t.add_row("Driver", f"{driver_name}  (address: {driver_config.get('address', '?')})")

        fresh_style = FRESHNESS_STYLES.get(freshness, C_NOTE)
        t.add_row("Freshness", f"[{fresh_style}]{freshness.value}[/{fresh_style}]")

        if reading is None:
            t.add_row("Status", f"[{C_DEAD}]No readings received[/{C_DEAD}]")
            return t

        # Track delivery interval
        if last_sample_time is not None and reading.sample_time != last_sample_time:
            gap = reading.sample_time - last_sample_time
            intervals.append(gap)
            # Keep last 20
            if len(intervals) > 20:
                intervals.pop(0)
        if reading.sample_time != (last_sample_time or 0):
            reading_count += 1
            last_sample_time = reading.sample_time

        age = reading.age
        t.add_row("Age", f"{age:.1f}s")
        t.add_row("Readings", str(reading_count))

        if intervals:
            avg_interval = sum(intervals) / len(intervals)
            t.add_row(
                "Delivery",
                f"avg {avg_interval:.1f}s  (expected {delivery_interval}s, "
                f"n={len(intervals)})",
            )

        t.add_row("", "")  # Spacer

        # Temperature
        temp_k = reading.get("temperature")
        if temp_k is not None:
            temp_f = kelvin_to_fahrenheit(temp_k)
            if raw:
                t.add_row("Temperature", f"{temp_f:.2f}°F  ({temp_k:.2f} K)")
            else:
                t.add_row("Temperature", f"{temp_f:.2f}°F")

        # Humidity
        ah = reading.get("absolute_humidity")
        if ah is not None:
            if raw and temp_k is not None and physics_registry.has_plugin("humidity"):
                rh = physics_registry.to_derived("humidity", ah, temperature=temp_k)
                t.add_row("Humidity", f"{ah:.2f} g/m³  ({rh:.1f}%RH)")
            else:
                t.add_row("Humidity", f"{ah:.2f} g/m³")

        # Battery
        bat = reading.get("battery")
        if bat is not None:
            bat_style = C_OK if bat > 20 else (C_WARN if bat > 5 else C_DEAD)
            t.add_row("Battery", f"[{bat_style}]{bat:.0f}%[/{bat_style}]")

        # Signal
        rssi = reading.get("signal_strength")
        if rssi is not None:
            rssi_style = C_OK if rssi > -70 else (C_WARN if rssi > -85 else C_DEAD)
            t.add_row("RSSI", f"[{rssi_style}]{rssi:.0f} dBm[/{rssi_style}]")

        return t

    console.print(f"[{C_NOTE}]Watching {sensor_name}... Ctrl-C to stop[/{C_NOTE}]\n")

    import time as _time
    start = _time.time()

    try:
        with Live(build_display(), console=console, refresh_per_second=2) as live:
            while True:
                _time.sleep(0.5)
                live.update(build_display())
                if duration and (_time.time() - start) >= duration:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        # Stop the BLE scanner cleanly
        try:
            from spriggler.sensors.govee import GoveeSensor
            GoveeSensor.stop_scanner()
        except Exception:
            pass

    console.print(f"\n[{C_NOTE}]Stopped after {reading_count} readings[/{C_NOTE}]")


@sensor.command("test")
@click.argument("sensor_name")
@click.option(
    "--duration", "-d",
    type=int,
    default=120,
    help="Test duration in seconds.  Default: 120.",
)
@click.pass_context
def sensor_test(ctx, sensor_name, duration):
    """Verify sensor connectivity and data quality.

    Runs a timed test that measures:
      • Connectivity: did we receive any readings?
      • Delivery rate: readings/minute vs expected
      • Delivery regularity: σ of inter-reading interval
      • Value stability: noise level when conditions are stable
      • Freshness: what percentage of time was data fresh/aging/stale?

    \b
    Pass/fail criteria:
      PASS:  >80% fresh readings, delivery rate within 2× expected
      WARN:  >50% fresh, or delivery rate within 5× expected
      FAIL:  <50% fresh, or no readings received

    \b
    Examples:
      spriggler sensor test seedling_sensor
      spriggler sensor test ambient_sensor -d 300
    """
    in_development(
        command="spriggler sensor test",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Automated sensor health check.  Run after setting up a new "
            "sensor or when you suspect connectivity issues.  Uses the "
            "same driver and freshness classification as the controller."
        ),
        depends_on=[
            "Sensor drivers",
            "Sensor freshness classification",
        ],
    )


@sensor.command("history")
@click.argument("sensor_name")
@click.option(
    "--hours",
    type=float,
    default=1.0,
    help="Hours of history to show.  Default: 1.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "csv", "json"]),
    default="table",
    help="Output format.  Default: table.",
)
@click.pass_context
def sensor_history(ctx, sensor_name, hours, fmt):
    """Show recent reading history for a sensor.

    Displays timestamped readings from the daemon's sensor log.
    Useful for post-hoc analysis of sensor behavior.
    """
    in_development(
        command="spriggler sensor history",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Reads from the daemon's structured log to reconstruct "
            "sensor history.  The CSV output is useful for plotting "
            "in external tools."
        ),
        depends_on=[
            "Structured logging (sensor events)",
            "spriggler start (daemon must have run to generate history)",
        ],
    )
