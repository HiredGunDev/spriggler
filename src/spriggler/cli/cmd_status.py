"""spriggler status — show current system state."""

import click

from spriggler.cli._style import in_development


@click.group(invoke_without_command=True)
@click.pass_context
def status(ctx):
    """Show current system status.

    Without a subcommand, shows a summary dashboard: daemon state,
    environment conditions, device states, sensor freshness, and
    any active alerts.

    \b
    Subcommands:
      spriggler status              Full dashboard (default)
      spriggler status sensors      Sensor detail: values, freshness, fusion
      spriggler status devices      Device detail: states, verification, power
      spriggler status envs         Environment detail: targets, compliance
      spriggler status drift        Calibration drift indicators (EWMA)
      spriggler status alerts       Active alerts and warnings
    """
    if ctx.invoked_subcommand is None:
        _status_dashboard(ctx)


def _status_dashboard(ctx):
    in_development(
        command="spriggler status",
        phase="Phase 0 / Phase 1 (incremental)",
        summary=(
            "Reads status.json (written each cycle by the daemon) and "
            "renders a color-coded dashboard.\n\n"
            "Phase 0 deliverable: sensor readings and freshness.\n"
            "Phase 1 deliverable: full dashboard with environments, "
            "devices, targets, compliance, and alerts.\n\n"
            "The dashboard is the operator's primary view of the system.  "
            "Color coding:\n"
            "  • Green:  in target / healthy / fresh\n"
            "  • Yellow: approaching limit / aging / warning\n"
            "  • Red:    out of range / stale-dead / alert\n"
            "  • Dim:    inactive / not applicable"
        ),
        notes=(
            "status.json is the contract between the daemon and all "
            "consumers (CLI, web UI, monitoring).  It contains the "
            "complete snapshot of system state written atomically each "
            "cycle.  The CLI reads it; the daemon writes it.\n\n"
            "If the daemon isn't running, status reads the last-written "
            "status.json and shows the timestamp + 'daemon not running' "
            "warning."
        ),
        depends_on=[
            "status.json schema definition",
            "spriggler start  (daemon writes status.json)",
        ],
    )


@status.command("sensors")
@click.option(
    "--environment", "-e",
    help="Filter to a specific environment.",
)
@click.option(
    "--raw",
    is_flag=True,
    help="Show raw sensor values (before physics conversion and fusion).",
)
@click.pass_context
def status_sensors(ctx, environment, raw):
    """Show detailed sensor status.

    For each sensor: current value, sample timestamp, freshness
    classification, delivery rate vs expected, and fusion weight.

    \b
    Freshness classification (thresholds from sensor delivery interval):
      Fresh:  age < 1.5× interval  — full confidence, normal control
      Aging:  age < 3× interval    — suppress aggressive actions
      Stale:  age < 10× interval   — hold state, no new decisions
      Dead:   age ≥ 10× interval   — safe mode, energy devices off

    \b
    With --raw: shows the original sensor units (e.g., %RH) before
    physics plugin conversion to fundamental quantities (e.g., g/m³).
    """
    in_development(
        command="spriggler status sensors",
        phase="Phase 0 (Sensor Freshness Tracking)",
        summary=(
            "Displays per-sensor detail including raw readings, converted "
            "fundamental values, sample timestamps, freshness class, "
            "delivery statistics, and Kalman filter weights.\n\n"
            "This is the first status subcommand we'll implement because "
            "sensor freshness is foundational to everything else.  If you "
            "can't trust your sensors, you can't trust your control."
        ),
        notes=(
            "Govee BLE sensors deliver sporadically — sometimes 15s, "
            "sometimes 90s.  The freshness display shows both the "
            "current age and a rolling delivery rate so you can see "
            "if a sensor is degrading over time.\n\n"
            "The --raw flag is important during calibration and debugging.  "
            "You need to see what the sensor actually reports (%RH) "
            "alongside what the controller sees (absolute humidity in g/m³)."
        ),
        salvage_from_v04=[
            "sensors/govee.py — BLE scanner with _sample_time tracking",
        ],
    )


@status.command("devices")
@click.option(
    "--environment", "-e",
    help="Filter to a specific environment.",
)
@click.pass_context
def status_devices(ctx, environment):
    """Show detailed device status.

    For each device: current state, commanded state, verification
    status, last command time, power draw (if sensed), and
    control reason (why this state was chosen).
    """
    in_development(
        command="spriggler status devices",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Displays per-device detail including current vs commanded "
            "state, verification status (confirmed/pending/retrying), "
            "last command timestamp, power draw, and the controller's "
            "reason for the current state.\n\n"
            "The 'reason' field is critical for debuggability — every "
            "device state must be explainable in plain English:\n"
            "  'heater ON: temperature 71.2°F below turn-on threshold "
            "72.0°F (target max 76.0°F minus coast 2.8°F minus "
            "hysteresis 1.2°F)'"
        ),
        notes=(
            "Verification status is especially important for cloud-controlled "
            "devices like VeSync.  The display shows:\n"
            "  • Confirmed: sensor feedback matches commanded state\n"
            "  • Pending: within verification window, waiting\n"
            "  • Retrying: verification failed, command resent\n"
            "  • Unverified: no sensor feedback available"
        ),
        depends_on=[
            "spriggler start (daemon writes device states to status.json)",
            "Device drivers (KASA, VeSync)",
            "Actuator verification system",
        ],
    )


@status.command("envs")
@click.option(
    "--environment", "-e",
    help="Show detail for a specific environment only.",
)
@click.pass_context
def status_envs(ctx, environment):
    """Show environment status: conditions vs targets.

    For each environment: current property values, target ranges,
    compliance status, active devices, and ambient conditions.
    """
    in_development(
        command="spriggler status envs",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Per-environment display showing each controlled property's "
            "current value, target range, and compliance.\n\n"
            "Values shown in both fundamental (g/m³) and derived (%RH) "
            "units for readability.  The controller works in fundamental; "
            "the display shows what's intuitive."
        ),
        depends_on=[
            "spriggler start (daemon running)",
            "Physics plugins (for derived unit display)",
        ],
    )


@status.command("drift")
@click.pass_context
def status_drift(ctx):
    """Show calibration drift indicators.

    Displays EWMA prediction error for each actuator's calibrated
    parameters (rates, coast profiles).  Flags devices that may
    need recalibration.
    """
    in_development(
        command="spriggler status drift",
        phase="Phase 1+ (Calibration Drift Detection)",
        summary=(
            "Reads EWMA prediction error tracking from the daemon and "
            "displays per-device drift indicators.\n\n"
            "Each actuator's calibrated rate and coast profile is compared "
            "against actual outcomes.  The EWMA smooths noise; a sustained "
            "divergence flags the device for recalibration.\n\n"
            "Display shows:\n"
            "  • Per-device EWMA error (rate drift, coast drift)\n"
            "  • Baseline σ from initial calibration\n"
            "  • Status: OK / Warning (>2σ) / Recalibrate (>3σ)\n"
            "  • Trend direction: device weakening, coast extending, etc."
        ),
        notes=(
            "EWMA λ = 0.2 default (configurable).  Alarm threshold at 3σ "
            "of baseline prediction error.  This is standard industrial "
            "process control practice — not a magic constant.\n\n"
            "Important: if ALL devices show drift in the same direction, "
            "the sensor has likely drifted, not the devices.  The display "
            "should flag this pattern."
        ),
    )


@status.command("alerts")
@click.pass_context
def status_alerts(ctx):
    """Show active alerts and warnings.

    \b
    Alert categories:
      SAFETY:  Near or at safety limits — immediate attention
      SENSOR:  Dead or persistently stale sensor
      DEVICE:  Verification failure, device unresponsive
      DRIFT:   Calibration drift detected
      SYSTEM:  Daemon health, disk space, etc.
    """
    in_development(
        command="spriggler status alerts",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Aggregated alert view pulled from status.json.  Alerts are "
            "categorized by severity (critical/warning/info) and source "
            "(safety/sensor/device/drift/system).\n\n"
            "Critical alerts are also logged to syslog and can trigger "
            "external notifications (Phase 3+: email, webhook, MQTT)."
        ),
        depends_on=[
            "Safety monitor subsystem",
            "Sensor freshness tracking",
            "Actuator verification",
            "Calibration drift detection",
        ],
        salvage_from_v04=[
            "safety/monitor.py — Safety limits, lockouts (concept, may rewrite)",
        ],
    )
