"""spriggler env — environment management and inspection."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def env(ctx):
    """Environment management and inspection.

    Environments are the physical spaces Spriggler controls.  These
    commands show the topology, connections, and relationships between
    environments.

    \b
    Subcommands:
      spriggler env list        List configured environments
      spriggler env show        Show environment detail and topology
      spriggler env connections Show inter-environment connections
    """
    pass


@env.command("list")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.  Default: table.",
)
@click.pass_context
def env_list(ctx, fmt):
    """List all configured environments.

    \b
    Shows per environment:
      • Name and description
      • Media present (air, water, soil, etc.)
      • Number of sensors and devices
      • Number of connections to other environments
      • Calibration status (calibrated / uncalibrated / stale)
    """
    in_development(
        command="spriggler env list",
        phase="Phase 0 / Phase 1",
        summary=(
            "Simple environment listing from config.  Shows the declared "
            "topology at a glance.\n\n"
            "Phase 0: basic list from config.\n"
            "Phase 1: adds calibration status and runtime state."
        ),
    )


@env.command("show")
@click.argument("environment_name")
@click.pass_context
def env_show(ctx, environment_name):
    """Show detailed environment information.

    \b
    Displays:
      • Media and their properties
      • All sensors: name, type, properties, delivery interval
      • All devices: name, type, intended properties, states
      • Connections: to which environments, via what medium,
        passive conductance, active transfer devices
      • Targets: per-medium, per-property ranges
      • Safety limits
      • Schedules: device schedules affecting this environment
      • Calibration summary: key rates and time constants
    """
    in_development(
        command="spriggler env show",
        phase="Phase 0 / Phase 1",
        summary=(
            "Complete environment detail view.  This is the 'everything "
            "about this environment' command — pulls from config, "
            "calibration, and runtime state to give the full picture.\n\n"
            "The connections view is especially important for Phase 3 "
            "(multi-environment) — understanding the topology is key "
            "to understanding transfer device behavior."
        ),
    )


@env.command("connections")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "graph"]),
    default="table",
    help="Output format.  'graph' renders an ASCII topology diagram.",
)
@click.pass_context
def env_connections(ctx, fmt):
    """Show inter-environment connections.

    \b
    Table format shows each connection with:
      • Endpoints (environment A ↔ environment B, or env ↔ ambient)
      • Medium transported
      • Passive conductance (if calibrated)
      • Active transfer device (if any)
      • Current differential (if daemon running)

    \b
    Graph format renders an ASCII network diagram showing
    environments as nodes and connections as edges.

    \b
    Examples:
      spriggler env connections
      spriggler env connections --format graph
    """
    in_development(
        command="spriggler env connections",
        phase="Phase 1 / Phase 3 (Multi-Environment)",
        summary=(
            "Connection topology viewer.  The table format is useful for "
            "single-environment setups (environment ↔ ambient).  The graph "
            "format shines in multi-environment setups (Phase 3) where "
            "understanding the topology is essential.\n\n"
            "ASCII graph example:\n"
            "  [seedling] ──fan──▶ [ambient]\n"
            "  [veg]      ──fan──▶ [ambient]\n"
            "  [veg]      ──duct─▶ [freezer]\n\n"
            "With runtime data, the graph shows current differentials "
            "and flow directions."
        ),
    )
