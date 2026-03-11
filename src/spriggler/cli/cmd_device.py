"""spriggler device — discover, test, and manually control devices."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def device(ctx):
    """Discover, test, and manually control devices.

    Devices are the system's hands.  These commands let you find
    available devices, test connectivity, and manually control them
    independently of the controller daemon.

    \b
    Subcommands:
      spriggler device scan       Discover devices on the network
      spriggler device test       Verify device connectivity and control
      spriggler device set        Manually set a device state
      spriggler device info       Show device details and capabilities
      spriggler device power      Read power consumption (if available)
    """
    pass


@device.command("scan")
@click.option(
    "--type", "device_type",
    type=click.Choice(["kasa", "vesync", "all"]),
    default="all",
    help="Device type to scan for.  Default: all.",
)
@click.pass_context
def device_scan(ctx, device_type):
    """Discover devices on the network.

    KASA devices are discovered via local UDP broadcast.
    VeSync devices are discovered via cloud API login.

    \b
    For each device found:
      • Name / alias
      • Type (plug, strip, humidifier, etc.)
      • IP address (KASA) or device ID (VeSync)
      • Current state (on/off, power draw)
      • Whether already configured in Spriggler

    \b
    Examples:
      spriggler device scan
      spriggler device scan --type kasa
    """
    in_development(
        command="spriggler device scan",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Network device discovery.  KASA uses local UDP broadcast "
            "(python-kasa), VeSync requires cloud API authentication "
            "(pyvesync).\n\n"
            "KASA smart strips enumerate their individual plugs — you'll "
            "see each plug as a separately controllable device.  The "
            "seedling KASA KP303 strip has plugs named Heater, Lights, "
            "Fan.\n\n"
            "VeSync Dual 200S humidifier shows as a single device with "
            "states: off, low (sleep mode), high (humidity mode)."
        ),
        salvage_from_v04=[
            "devices/kasa.py — KASA discovery and connection manager",
            "devices/vesync.py — VeSync rate limiter wrapper",
        ],
    )


@device.command("test")
@click.argument("device_name")
@click.option(
    "--cycle",
    is_flag=True,
    help="Cycle through all device states (off → on → off, or "
         "off → low → high → off).  Pauses between states for "
         "visual confirmation.",
)
@click.pass_context
def device_test(ctx, device_name, cycle):
    """Verify device connectivity and control.

    Sends a command to the device and verifies it responds.
    With --cycle, steps through all available states.

    \b
    For KASA devices: tests local network control.
    For VeSync devices: tests cloud API round-trip.

    \b
    Examples:
      spriggler device test heater
      spriggler device test humidifier --cycle
    """
    in_development(
        command="spriggler device test",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Device connectivity and control verification.  Critical "
            "for VeSync cloud devices where commands can silently fail.\n\n"
            "The --cycle test is especially useful for graduated devices "
            "(humidifier off → low → high → off) to verify all states "
            "are reachable."
        ),
        depends_on=[
            "Device drivers (KASA, VeSync)",
        ],
    )


@device.command("set")
@click.argument("device_name")
@click.argument("state")
@click.option(
    "--force",
    is_flag=True,
    help="Set state even if the daemon is running (overrides controller).  "
         "The daemon will reassert control on its next cycle.",
)
@click.pass_context
def device_set(ctx, device_name, state, force):
    """Manually set a device to a specific state.

    \b
    States depend on device type:
      Binary devices:    on, off
      Graduated devices: off, low, high (or device-specific names)

    \b
    If the daemon is running, this command is refused unless --force
    is used.  With --force, the daemon will reassert its own control
    decision on the next cycle, so the manual override is temporary.

    \b
    Examples:
      spriggler device set heater on
      spriggler device set humidifier low
      spriggler device set fan off --force
    """
    in_development(
        command="spriggler device set",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Manual device control for testing and emergency override.\n\n"
            "Safety check: refuses if daemon is running unless --force "
            "is specified.  Two concurrent controllers (daemon + manual) "
            "create unpredictable behavior.  The --force flag acknowledges "
            "the risk and lets the daemon reassert on next cycle."
        ),
        depends_on=[
            "Device drivers (KASA, VeSync)",
        ],
    )


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
      • Calibrated rates and coast profiles (if calibrated)
      • Current state and power draw (if reachable)
      • Control history (last N state changes)
    """
    in_development(
        command="spriggler device info",
        phase="Phase 0 / Phase 1 (incremental)",
        summary=(
            "Comprehensive device info pulling from config (declared "
            "properties), calibration (discovered rates), and runtime "
            "state (current state, power).\n\n"
            "Phase 0: config and live state.  Phase 1: adds calibration "
            "data and control history."
        ),
    )


@device.command("power")
@click.argument("device_name", required=False)
@click.option(
    "--watch",
    is_flag=True,
    help="Continuously monitor power draw.  Ctrl-C to stop.",
)
@click.pass_context
def device_power(ctx, device_name, watch):
    """Read power consumption from devices.

    KASA smart strips and plugs with energy monitoring report real-time
    power draw in watts.  Without a device name, shows all devices
    with power monitoring.

    \b
    Examples:
      spriggler device power             All devices with power monitoring
      spriggler device power heater      Single device
      spriggler device power --watch     Live power monitoring
    """
    in_development(
        command="spriggler device power",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Power monitoring from KASA energy-monitoring plugs/strips.  "
            "The KP303 strip reports per-plug power draw.\n\n"
            "During calibration, power readings are used to estimate "
            "thermal byproduct for devices where power draw is available.  "
            "During runtime, power anomalies (device drawing no power when "
            "commanded ON) can assist actuator verification."
        ),
        salvage_from_v04=[
            "calibrate/power.py — Power draw measurement",
        ],
    )
