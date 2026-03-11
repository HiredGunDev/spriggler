"""spriggler physics — physics plugin library tools."""

import click

from spriggler.cli._style import in_development


@click.group()
@click.pass_context
def physics(ctx):
    """Physics plugin library: property conversions and calculations.

    Physics plugins handle the conversion between derived quantities
    (what sensors report and humans understand) and fundamental
    quantities (what the controller works in internally).

    \b
    The core insight: relative humidity (%RH) changes when temperature
    changes, even if moisture content is constant.  Working in absolute
    humidity (g/m³) eliminates phantom cross-effects.

    \b
    Subcommands:
      spriggler physics list       Show available physics plugins
      spriggler physics convert    Convert between units interactively
      spriggler physics info       Show details of a specific plugin
    """
    pass


@physics.command("list")
@click.pass_context
def physics_list(ctx):
    """Show available physics plugins.

    \b
    Each plugin declares:
      • Derived property (e.g., relative_humidity)
      • Fundamental property it maps to (e.g., absolute_humidity)
      • Required co-properties (e.g., temperature)
      • Conversion functions (derived → fundamental, fundamental → derived)

    \b
    Built-in plugins:
      rh_to_ah     %RH ↔ absolute humidity (requires temperature)
      do_sat       %DO-saturation ↔ DO concentration (requires water temp)
    """
    in_development(
        command="spriggler physics list",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Lists all registered physics plugins.  Initially two:\n\n"
            "  1. rh_to_ah: Converts %RH (relative humidity) to absolute "
            "humidity (g/m³) using the Magnus formula.  Requires air "
            "temperature as a co-property.  This is the plugin that "
            "eliminates the phantom cross-effect.\n\n"
            "  2. do_sat: Converts %DO-saturation to dissolved oxygen "
            "concentration (mg/L).  Requires water temperature.  For "
            "aquaculture applications.\n\n"
            "Plugin architecture: each plugin is a Python module in "
            "spriggler/physics/ that registers its conversions.  Adding "
            "new domain-specific conversions (dew point, VPD, water "
            "activity) requires adding a module, not modifying core code."
        ),
    )


@physics.command("convert")
@click.argument("value", type=float)
@click.argument("from_unit")
@click.argument("to_unit")
@click.option(
    "--temperature", "-t",
    type=float,
    help="Temperature for humidity conversions (°F or °C, auto-detected).",
)
@click.pass_context
def physics_convert(ctx, value, from_unit, to_unit, temperature):
    """Convert between units using physics plugins.

    Interactive unit conversion using the same physics plugins the
    controller uses internally.  Useful for sanity-checking calibration
    data or understanding sensor readings.

    \b
    Examples:
      spriggler physics convert 65 rh ah -t 75       # 65%RH → g/m³ at 75°F
      spriggler physics convert 12.5 ah rh -t 75     # 12.5 g/m³ → %RH at 75°F
      spriggler physics convert 75 fahrenheit kelvin  # temperature conversion
      spriggler physics convert 8.2 do_mgL do_sat -t 68  # DO → %sat at 68°F
    """
    in_development(
        command="spriggler physics convert",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Command-line unit converter using the physics plugins.  "
            "Same code paths as the sensor boundary conversion in the "
            "controller — so this is also a test tool for the plugins.\n\n"
            "Supports:\n"
            "  • %RH ↔ absolute humidity (g/m³) at a given temperature\n"
            "  • Temperature: °F ↔ °C ↔ K\n"
            "  • %DO-saturation ↔ DO concentration (mg/L)\n\n"
            "The Magnus formula for %RH ↔ absolute humidity:\n"
            "  AH = (6.112 × e^((17.67 × T)/(T + 243.5)) × RH × 2.1674) "
            "/ (273.15 + T)\n"
            "  where T is temperature in °C and RH is 0-100."
        ),
    )


@physics.command("info")
@click.argument("plugin_name")
@click.pass_context
def physics_info(ctx, plugin_name):
    """Show details of a specific physics plugin.

    \b
    Displays:
      • Derived and fundamental properties
      • Required co-properties
      • Mathematical formula
      • Valid input ranges
      • Accuracy notes and limitations
    """
    in_development(
        command="spriggler physics info",
        phase="Phase 0 (CLI and Calibration)",
        summary=(
            "Detailed plugin documentation.  Shows the conversion formula, "
            "valid ranges, and any caveats (e.g., the Magnus formula is "
            "accurate to ±0.4% for -45°C to 60°C).\n\n"
            "Also shows example conversions at common operating points "
            "for the relevant domain."
        ),
    )
