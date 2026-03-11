"""spriggler log — view and manage structured logs."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def log(ctx):
    """View and manage structured logs.

    Spriggler logs all events as structured JSON.  These commands
    provide filtered, human-readable views of the log stream.

    \b
    Subcommands:
      spriggler log tail        Follow the live log stream
      spriggler log search      Search logs by time, level, or content
      spriggler log decisions   Show controller decision history
      spriggler log export      Export logs to file
    """
    pass


@log.command("tail")
@click.option(
    "--level", "-l",
    type=click.Choice(["debug", "info", "warning", "error"]),
    default="info",
    help="Minimum log level.  Default: info.",
)
@click.option(
    "--filter", "-f", "log_filter",
    multiple=True,
    help="Filter by component (e.g., 'sensor', 'device.kasa', "
         "'controller.energy').  May be repeated for OR filtering.",
)
@click.option(
    "--environment", "-e",
    help="Filter to a specific environment.",
)
@click.pass_context
def log_tail(ctx, level, log_filter, environment):
    """Follow the live log stream.

    Color-coded by level: debug (dim), info (default), warning
    (yellow), error (red).  Structured fields are rendered inline
    for readability.

    \b
    Examples:
      spriggler log tail
      spriggler log tail -l debug
      spriggler log tail -f sensor -f device
      spriggler log tail -e seedling -l warning
    """
    in_development(
        command="spriggler log tail",
        phase="Phase 0 / Phase 1",
        summary=(
            "Live log follower with filtering and color coding.  "
            "Reads from the daemon's structured log file and renders "
            "JSON events as human-readable lines.\n\n"
            "Phase 0: basic log tailing with level filtering.\n"
            "Phase 1: component filtering, environment filtering, "
            "structured field rendering."
        ),
        salvage_from_v04=[
            "struct_log.py — Structured JSON logger",
        ],
    )


@log.command("search")
@click.option(
    "--since",
    type=str,
    help="Start time (ISO format or relative: '1h', '30m', '2d').",
)
@click.option(
    "--until",
    type=str,
    help="End time (ISO format or relative).",
)
@click.option(
    "--level", "-l",
    type=click.Choice(["debug", "info", "warning", "error"]),
    default=None,
    help="Filter by minimum log level.",
)
@click.option(
    "--component", "-c",
    help="Filter by component name.",
)
@click.option(
    "--grep", "-g",
    help="Full-text search within log messages.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.  Default: text.",
)
@click.pass_context
def log_search(ctx, since, until, level, component, grep, fmt):
    """Search logs by time, level, or content.

    \b
    Examples:
      spriggler log search --since 1h               Last hour
      spriggler log search --since 1h -l error       Errors in last hour
      spriggler log search --since 2d -c calibrate   Calibration logs, 2 days
      spriggler log search -g "coast"                Full-text search
    """
    in_development(
        command="spriggler log search",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Structured log search with time-range, level, component, "
            "and full-text filters.  Returns matching events rendered "
            "as human-readable text or raw JSON.\n\n"
            "Relative time expressions ('1h', '30m', '2d') are converted "
            "to absolute timestamps for the search."
        ),
    )


@log.command("decisions")
@click.option(
    "--since",
    type=str,
    default="1h",
    help="How far back to show.  Default: 1h.",
)
@click.option(
    "--device", "-d",
    help="Filter to decisions about a specific device.",
)
@click.option(
    "--environment", "-e",
    help="Filter to a specific environment.",
)
@click.pass_context
def log_decisions(ctx, since, device, environment):
    """Show controller decision history.

    Every control decision is logged with its reasoning: which
    thresholds were evaluated, what the sensor values were, why
    a particular device state was chosen, and what the predicted
    outcome is.

    \b
    This is the primary debugging tool.  If the controller does
    something unexpected, 'log decisions' shows you why.

    \b
    Each decision entry shows:
      • Timestamp
      • Device and commanded state
      • Trigger: which threshold was crossed
      • Sensor values at decision time (with freshness)
      • Reasoning: distance from target, coast estimate, differential
      • Predicted outcome

    \b
    Examples:
      spriggler log decisions                     Last hour, all
      spriggler log decisions --since 4h -d heater
      spriggler log decisions -e seedling --since 30m
    """
    in_development(
        command="spriggler log decisions",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Decision audit trail.  The controller logs every state "
            "change decision with complete context — this command renders "
            "those decision events in a readable format.\n\n"
            "This is the v0.5 answer to the v0.4 problem of opaque "
            "controller behavior.  The trajectory planner's decisions "
            "were unpredictable because cost functions interacted in "
            "complex ways.  The hysteresis controller's decisions are "
            "simple: 'temperature 71.2°F is below turn-on threshold "
            "72.0°F, heater ON.'  This command shows those decisions."
        ),
        notes=(
            "Engineering principle: 'If the code can't explain what it's "
            "doing in plain English, the design is wrong.'  This command "
            "is the test of that principle — every decision must be "
            "expressible as a simple, readable statement."
        ),
    )


@log.command("export")
@click.argument("output_file", type=click.Path())
@click.option(
    "--since",
    type=str,
    help="Start time.  Default: all logs.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["jsonl", "csv"]),
    default="jsonl",
    help="Export format.  Default: JSON Lines.",
)
@click.pass_context
def log_export(ctx, output_file, since, fmt):
    """Export logs to file for external analysis.

    JSON Lines (one JSON object per line) is the default — it's
    parseable by jq, pandas, and most log analysis tools.  CSV
    is available for spreadsheet use.
    """
    in_development(
        command="spriggler log export",
        phase="Phase 1 (Single Environment Controller)",
        summary=(
            "Bulk log export for external analysis.  JSONL is preferred "
            "for structured data; CSV flattens the nested structure for "
            "spreadsheet use.\n\n"
            "Use case: analyzing a night's worth of controller behavior "
            "in a Jupyter notebook or spreadsheet."
        ),
    )
