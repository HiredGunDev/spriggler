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
      spriggler sensor scan       Scan for available sensors (BLE, network)
      spriggler sensor watch      Live-stream readings from a sensor
      spriggler sensor test       Verify sensor connectivity and data quality
      spriggler sensor history    Show recent reading history for a sensor
    """
    pass


@sensor.command("scan")
@click.option(
    "--type", "sensor_type",
    type=click.Choice(["ble", "network", "all"]),
    default="all",
    help="Sensor type to scan for.  Default: all.",
)
@click.option(
    "--duration",
    type=int,
    default=30,
    help="Scan duration in seconds.  Default: 30.",
)
@click.pass_context
def sensor_scan(ctx, sensor_type, duration):
    """Scan for available sensors.

    BLE scan discovers Govee and other BLE environmental sensors.
    Network scan discovers wired sensors on the local network.

    \b
    For BLE sensors, shows:
      • MAC address
      • Device name / model
      • Signal strength (RSSI)
      • Last advertised reading (if available)
      • Whether already configured in Spriggler

    \b
    Examples:
      spriggler sensor scan                 Scan all types, 30s
      spriggler sensor scan --type ble      BLE only
      spriggler sensor scan --duration 60   Longer scan for weak signals
    """
    in_development(
        command="spriggler sensor scan",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "BLE discovery using bleak.  Scans for BLE advertisements "
            "matching known sensor profiles (Govee H5075, H5074, etc.).\n\n"
            "The Govee H5075 embeds temperature and humidity in its BLE "
            "advertisement data — we decode it during scan to show live "
            "readings.  This lets you identify sensors before configuring "
            "them.\n\n"
            "Cross-references discovered sensors against config to show "
            "which are already assigned and which are new."
        ),
        salvage_from_v04=[
            "sensors/govee.py — BLE scanner, advertisement decoding",
        ],
    )


@sensor.command("watch")
@click.argument("sensor_name")
@click.option(
    "--raw",
    is_flag=True,
    help="Show raw sensor values (before physics conversion).",
)
@click.option(
    "--duration",
    type=int,
    default=None,
    help="Watch duration in seconds.  Default: until Ctrl-C.",
)
@click.pass_context
def sensor_watch(ctx, sensor_name, raw, duration):
    """Live-stream readings from a sensor.

    Shows each reading as it arrives with timestamp, values, freshness
    classification, and delivery timing.  Useful for understanding
    sensor behavior and validating BLE connectivity.

    \b
    The display updates in-place (no scrolling) and shows:
      • Current reading (fundamental + derived units)
      • Sample timestamp and age
      • Freshness classification
      • Delivery interval (actual vs expected)
      • Rolling statistics (min, max, mean, σ)

    \b
    Examples:
      spriggler sensor watch pod_sensor
      spriggler sensor watch pod_sensor --raw
      spriggler sensor watch ambient --duration 300
    """
    in_development(
        command="spriggler sensor watch",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Real-time sensor monitoring.  Essential for:\n"
            "  • Verifying BLE connectivity (is the sensor reachable?)\n"
            "  • Understanding delivery patterns (how sporadic is BLE?)\n"
            "  • Checking physics conversion (does %RH → g/m³ look right?)\n"
            "  • Pre-calibration sanity check\n\n"
            "Uses Rich live display for in-place updates.  The rolling "
            "statistics help identify sensor noise level and bias."
        ),
        depends_on=[
            "Sensor drivers (govee BLE, etc.)",
            "Physics plugin library (for unit conversion display)",
            "Sensor freshness classification",
        ],
    )


@sensor.command("test")
@click.argument("sensor_name")
@click.option(
    "--duration",
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
      spriggler sensor test pod_sensor
      spriggler sensor test ambient --duration 300
    """
    in_development(
        command="spriggler sensor test",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Automated sensor health check.  Run this after setting up "
            "a new sensor or when you suspect connectivity issues.\n\n"
            "The pass/fail criteria are conservative — a sensor that's "
            "'WARN' will work but may cause the controller to suppress "
            "aggressive actions more often due to aging data."
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
            "sensor history.  Displays readings, freshness events, "
            "and any anomalies (missed deliveries, outlier values).\n\n"
            "The CSV output is useful for plotting in external tools "
            "or spreadsheets."
        ),
        depends_on=[
            "Structured logging (sensor events)",
            "spriggler start (daemon must have run to generate history)",
        ],
    )
