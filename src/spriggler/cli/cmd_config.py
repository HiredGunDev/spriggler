"""spriggler config — manage system configuration."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def config(ctx):
    """Manage Spriggler configuration.

    Configuration declares the physical topology: environments, media,
    connections, devices, sensors, targets, and safety limits.  The
    controller discovers device CHARACTERISTICS through calibration,
    but the user declares device ROLES and TOPOLOGY through config.

    \b
    Config location: $SPRIGGLER_CONFIG_DIR/config.yaml
    Default: ~/.spriggler/config.yaml

    \b
    Subcommands:
      spriggler config init       Create a starter config (interactive)
      spriggler config validate   Validate config against schema
      spriggler config show       Display current config
      spriggler config edit       Open config in $EDITOR
      spriggler config diff       Show changes since last validated config
      spriggler config schema     Display the config JSON schema
    """
    pass


@config.command("init")
@click.option(
    "--template",
    type=click.Choice(["seedling", "veg-flower", "aquarium", "brewery", "blank"]),
    default=None,
    help="Start from a template.  Default: interactive wizard.",
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=None,
    help="Write config to this path instead of default location.",
)
@click.pass_context
def config_init(ctx, template, output):
    """Create a new Spriggler configuration.

    Without --template, runs an interactive wizard that walks through
    environment setup, device declaration, sensor assignment, and
    target ranges.

    \b
    Templates provide starting points for common setups:
      seedling     Small grow tent: heater, light, fan, humidifier
      veg-flower   Larger grow: HPS lights, multi-zone, CO₂
      aquarium     Water temperature, DO, pH
      brewery      Fermentation temperature control
      blank        Minimal skeleton

    \b
    Examples:
      spriggler config init                     Interactive wizard
      spriggler config init --template seedling  Start from seedling template
      spriggler config init -o ./my-config.yaml  Write to specific path
    """
    in_development(
        command="spriggler config init",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Interactive config creation.  The wizard asks for:\n\n"
            "  1. Environments: name, description\n"
            "  2. Media per environment: air, water, soil, etc.\n"
            "  3. Connections: which environments connect, via what medium, "
            "any active transfer devices\n"
            "  4. Devices per environment: name, type (energy/transfer), "
            "driver (KASA/VeSync/GPIO), intended properties and directions, "
            "available states\n"
            "  5. Sensors: name, driver (govee/wired), environment, medium, "
            "properties reported, delivery interval\n"
            "  6. Targets: per environment, per medium, per property — "
            "min/max range\n"
            "  7. Safety limits: absolute boundaries that trigger "
            "emergency shutdown\n"
            "  8. Schedules: devices with time-based operation "
            "(lights on/off times)\n\n"
            "Templates pre-fill common configurations.  The seedling "
            "template matches our test hardware: Govee sensors, KASA "
            "strip, VeSync humidifier."
        ),
        notes=(
            "Config format is YAML for human editability.  Internally "
            "validated against a JSON Schema.  The schema enforces "
            "structural correctness; semantic validation (e.g., a "
            "device's intended property matches a property the sensor "
            "can measure) is done by 'config validate'.\n\n"
            "Convention over configuration: if a device has one state "
            "besides off, you don't declare states — it's binary "
            "(on/off).  If a sensor reports one property, you don't "
            "need to enumerate properties."
        ),
        salvage_from_v04=[
            "config/loader.py — JSON loading (needs significant revision "
            "for new YAML schema)",
        ],
    )


@config.command("validate")
@click.option(
    "--strict",
    is_flag=True,
    help="Fail on warnings (not just errors).",
)
@click.pass_context
def config_validate(ctx, strict):
    """Validate configuration against schema and physics.

    \b
    Checks three levels:
      1. Schema:   YAML structure, required fields, types
      2. Semantic:  Cross-references (devices reference real environments,
                    sensors measure properties that exist, connections
                    reference valid endpoints)
      3. Physics:   Sanity checks (energy device has at least one intended
                    property, transfer device connects two environments,
                    safety limits wider than target ranges)

    \b
    Warnings (non-fatal unless --strict):
      • Sensor without a matching device (can sense but can't control)
      • Device without a matching sensor (can control but can't verify)
      • Environment with no sensors (unobservable)
      • Target range narrower than sensor resolution × 2
    """
    in_development(
        command="spriggler config validate",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Three-tier validation: schema, semantic, and physics.  "
            "Must pass before calibration or daemon start.\n\n"
            "The semantic layer is where most user errors get caught — "
            "misspelled device names, dangling references, environments "
            "with no sensors.  Physics checks catch logical errors like "
            "a heater with 'decrease' direction on temperature.\n\n"
            "Output is color-coded:\n"
            "  • Green: all checks pass\n"
            "  • Yellow: warnings present (operational but suboptimal)\n"
            "  • Red: errors present (cannot proceed)"
        ),
    )


@config.command("show")
@click.option(
    "--section",
    type=click.Choice(["environments", "devices", "sensors", "connections",
                        "targets", "safety", "schedules", "all"]),
    default="all",
    help="Show a specific section.  Default: all.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["yaml", "json", "table"]),
    default="yaml",
    help="Output format.  Default: yaml.",
)
@click.pass_context
def config_show(ctx, section, fmt):
    """Display current configuration.

    Shows the loaded, validated config.  Use --section to focus on
    a specific part.  Use --format table for a human-readable summary
    rather than the raw YAML.
    """
    in_development(
        command="spriggler config show",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Pretty-prints the current config.  The table format renders "
            "environments, devices, sensors, connections, and targets as "
            "structured tables rather than raw YAML — useful for quick "
            "review."
        ),
    )


@config.command("edit")
@click.pass_context
def config_edit(ctx):
    """Open configuration in $EDITOR.

    After editing, automatically runs 'config validate' and reports
    any issues.  If validation fails, offers to revert.
    """
    in_development(
        command="spriggler config edit",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Opens $EDITOR (or vi) on the config file, then runs "
            "validation on save.  If the edited config is invalid, "
            "shows errors and offers to re-edit or revert.\n\n"
            "Backs up the previous config to config.yaml.bak before "
            "any edit."
        ),
    )


@config.command("diff")
@click.pass_context
def config_diff(ctx):
    """Show changes since last validated/active config.

    Compares the current config file against the last config that was
    validated and used by the daemon.  Highlights additions, removals,
    and changes.
    """
    in_development(
        command="spriggler config diff",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Semantic diff, not just text diff.  Shows:\n"
            "  • New environments, devices, sensors added\n"
            "  • Removed items\n"
            "  • Changed targets, safety limits, schedules\n"
            "  • Changed device properties or connections\n\n"
            "Color-coded: green for additions, red for removals, "
            "yellow for changes."
        ),
    )


@config.command("schema")
@click.option(
    "--format", "fmt",
    type=click.Choice(["json", "yaml"]),
    default="json",
    help="Schema output format.  Default: JSON Schema.",
)
@click.pass_context
def config_schema(ctx, fmt):
    """Display the configuration JSON schema.

    Useful for editor integration (YAML language server), external
    validation tools, or understanding the config structure.
    """
    in_development(
        command="spriggler config schema",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Dumps the JSON Schema that defines valid Spriggler config.  "
            "Can be used with YAML language servers in VS Code, vim, etc. "
            "for inline validation and autocompletion while editing config."
        ),
    )
