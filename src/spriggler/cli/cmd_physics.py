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
      spriggler physics convert    Convert between units
      spriggler physics info       Show details of a specific plugin
    """
    pass


def _load_plugins():
    """Run autodiscovery and return the physics registry."""
    from spriggler.util.discovery import discover_plugins
    from spriggler.physics import registry
    discover_plugins(package="spriggler.physics")
    return registry


# ── Unit aliases ─────────────────────────────────────────────────
# Map user-friendly names to internal conversion paths.

@physics.command("list")
@click.pass_context
def physics_list(ctx):
    """Show available physics plugins.

    Discovers all built-in and third-party physics plugins and
    displays their capabilities.
    """
    from rich.table import Table
    from rich import box
    from spriggler.cli._style import console, C_BRAND, C_CMD, C_NOTE

    reg = _load_plugins()
    plugins = reg.list_plugins()

    if not plugins:
        console.print(f"[{C_NOTE}]No physics plugins found.[/{C_NOTE}]")
        return

    t = Table(
        title="Physics Plugins",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Name", style=C_CMD)
    t.add_column("Derived → Fundamental")
    t.add_column("Co-properties")
    t.add_column("Description")

    for p in plugins:
        co = ", ".join(p.co_properties) if p.co_properties else "—"
        conversion = f"{p.derived_property} → {p.fundamental_property}"
        # Truncate description to first sentence
        desc = p.description.split(".")[0] + "." if "." in p.description else p.description
        t.add_row(p.name, conversion, co, desc)

    console.print(t)

    # Also show temperature conversions (not a plugin, always available)
    console.print(f"\n  [{C_NOTE}]Temperature conversions (built-in, always available):[/{C_NOTE}]")
    console.print(f"  [{C_NOTE}]  fahrenheit ↔ celsius ↔ kelvin[/{C_NOTE}]")


@physics.command("convert")
@click.argument("from_value")
@click.argument("to_unit")
@click.option(
    "--temperature", "-t",
    type=str,
    default=None,
    help="Temperature with unit for humidity conversions (e.g., 80F, 26.7C, 300K).",
)
@click.pass_context
def physics_convert(ctx, from_value, to_unit, temperature):
    """Convert between units using physics plugins.

    Values include their units — just like you'd say them.
    Uses the same conversion code the controller uses internally.

    \b
    Temperature:
      spriggler physics convert 75F K
      spriggler physics convert 24C F
      spriggler physics convert 297K F

    \b
    Humidity (requires -t with temperature and unit):
      spriggler physics convert 62%RH ah -t 75F
      spriggler physics convert 14.8ah %RH -t 75F
      spriggler physics convert 80%RH ah -t 26.7C

    \b
    Demonstrate the phantom cross-effect:
      spriggler physics convert 70%RH ah -t 75F    → note the g/m³
      spriggler physics convert 15.1ah %RH -t 80F  → %RH drops!
      (Same moisture, higher temp → lower %RH.  This killed v0.4.)
    """
    from spriggler.cli._style import console, C_OK, C_NOTE, C_WARN, C_ERROR
    from spriggler.physics.temperature import (
        fahrenheit_to_kelvin, kelvin_to_fahrenheit,
        celsius_to_kelvin, kelvin_to_celsius,
        fahrenheit_to_celsius, celsius_to_fahrenheit,
    )

    # Parse the input value + unit
    src_val, src_unit = _parse_value_unit(from_value)
    if src_val is None:
        console.print(f"[{C_ERROR}]Cannot parse '{from_value}'[/{C_ERROR}]")
        _show_usage(console, C_NOTE)
        raise SystemExit(1)

    # Normalize the target unit
    dst_unit = _normalize_unit(to_unit)
    if dst_unit is None:
        console.print(f"[{C_ERROR}]Unknown target unit: '{to_unit}'[/{C_ERROR}]")
        _show_usage(console, C_NOTE)
        raise SystemExit(1)

    # ── Temperature → Temperature ────────────────────────────
    if src_unit in _TEMP_UNITS and dst_unit in _TEMP_UNITS:
        result = _convert_temp(src_val, src_unit, dst_unit)
        console.print(
            f"[{C_OK}]{src_val}{_UNIT_SYMBOLS[src_unit]} = "
            f"{result:.4f}{_UNIT_SYMBOLS[dst_unit]}[/{C_OK}]"
        )
        return

    # ── Humidity → Humidity ──────────────────────────────────
    if src_unit in _HUM_UNITS and dst_unit in _HUM_UNITS:
        if temperature is None:
            console.print(
                f"[{C_ERROR}]Humidity conversions require temperature with unit.[/{C_ERROR}]\n"
                f"  Example: spriggler physics convert {from_value} {to_unit} -t 75F"
            )
            raise SystemExit(1)

        temp_val, temp_unit = _parse_value_unit(temperature)
        if temp_val is None or temp_unit not in _TEMP_UNITS:
            console.print(
                f"[{C_ERROR}]Cannot parse temperature: '{temperature}'[/{C_ERROR}]\n"
                f"  Use a number with unit: 75F, 24C, or 297K"
            )
            raise SystemExit(1)

        temp_k = _convert_temp(temp_val, temp_unit, "kelvin")

        # Range warning
        temp_c = temp_k - 273.15
        if temp_c < -45 or temp_c > 60:
            console.print(
                f"[{C_WARN}]Warning: {temp_val}{_UNIT_SYMBOLS[temp_unit]} "
                f"({temp_c:.1f}°C) is outside the Magnus formula's accurate "
                f"range (-45°C to 60°C).  Results may be unreliable.[/{C_WARN}]"
            )

        reg = _load_plugins()
        if not reg.has_plugin("humidity"):
            console.print(f"[{C_ERROR}]No humidity plugin loaded.[/{C_ERROR}]")
            raise SystemExit(1)

        temp_display = f"{temp_val}{_UNIT_SYMBOLS[temp_unit]}"

        if src_unit == "rh" and dst_unit == "ah":
            result = reg.to_fundamental("humidity", src_val, temperature=temp_k)
            console.print(
                f"[{C_OK}]{src_val}%RH at {temp_display} = {result:.4f} g/m³[/{C_OK}]"
            )
            console.print(
                f"  [{C_NOTE}](absolute humidity — what the controller uses internally)[/{C_NOTE}]"
            )
        elif src_unit == "ah" and dst_unit == "rh":
            result = reg.to_derived("humidity", src_val, temperature=temp_k)
            console.print(
                f"[{C_OK}]{src_val} g/m³ at {temp_display} = {result:.4f}%RH[/{C_OK}]"
            )
        else:
            console.print(f"[{C_OK}]{src_val} (no conversion needed)[/{C_OK}]")
        return

    # ── Mismatch ─────────────────────────────────────────────
    console.print(
        f"[{C_ERROR}]Cannot convert {_UNIT_SYMBOLS.get(src_unit, src_unit)} "
        f"→ {_UNIT_SYMBOLS.get(dst_unit, dst_unit)}[/{C_ERROR}]"
    )
    _show_usage(console, C_NOTE)
    raise SystemExit(1)


# ── Value+unit parsing ───────────────────────────────────────────

import re

# Units we recognize, grouped by domain
_TEMP_UNITS = {"fahrenheit", "celsius", "kelvin"}
_HUM_UNITS = {"rh", "ah"}

_UNIT_SYMBOLS = {
    "fahrenheit": "°F", "celsius": "°C", "kelvin": "K",
    "rh": "%RH", "ah": " g/m³",
}

# Patterns: number followed by optional unit suffix
# Handles: 75F, 75°F, 24.5C, 297K, 80%RH, 15.1ah, 15.1g/m3, 15.1g/m³
_VALUE_UNIT_RE = re.compile(
    r'^([+-]?\d+\.?\d*)\s*(.*)$'
)

# Map of suffix strings → canonical unit names
_SUFFIX_MAP = {
    # Temperature
    "f": "fahrenheit", "°f": "fahrenheit", "fahrenheit": "fahrenheit",
    "c": "celsius", "°c": "celsius", "celsius": "celsius",
    "k": "kelvin", "kelvin": "kelvin",
    # Humidity
    "rh": "rh", "%rh": "rh", "%": "rh", "percent": "rh",
    "ah": "ah", "g/m3": "ah", "g/m³": "ah", "g/m^3": "ah",
}


def _parse_value_unit(s: str) -> tuple[float | None, str | None]:
    """Parse a string like '75F' into (75.0, 'fahrenheit').

    Returns (None, None) if unparseable.
    """
    s = s.strip()
    m = _VALUE_UNIT_RE.match(s)
    if not m:
        return None, None

    value_str, unit_str = m.group(1), m.group(2).strip().lower()

    try:
        value = float(value_str)
    except ValueError:
        return None, None

    if not unit_str:
        return value, None

    unit = _SUFFIX_MAP.get(unit_str)
    if unit is None:
        return None, None

    return value, unit


def _normalize_unit(s: str) -> str | None:
    """Normalize a unit string to canonical name."""
    return _SUFFIX_MAP.get(s.strip().lower().replace("°", ""))


def _convert_temp(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between temperature units."""
    from spriggler.physics.temperature import (
        fahrenheit_to_kelvin, kelvin_to_fahrenheit,
        celsius_to_kelvin, kelvin_to_celsius,
        fahrenheit_to_celsius, celsius_to_fahrenheit,
    )
    if from_unit == to_unit:
        return value
    converters = {
        ("fahrenheit", "kelvin"): fahrenheit_to_kelvin,
        ("kelvin", "fahrenheit"): kelvin_to_fahrenheit,
        ("celsius", "kelvin"): celsius_to_kelvin,
        ("kelvin", "celsius"): kelvin_to_celsius,
        ("fahrenheit", "celsius"): fahrenheit_to_celsius,
        ("celsius", "fahrenheit"): celsius_to_fahrenheit,
    }
    return converters[(from_unit, to_unit)](value)


def _show_usage(console, note_style):
    """Show conversion usage examples."""
    console.print(f"\n[{note_style}]Usage examples:[/{note_style}]")
    console.print(f"  [{note_style}]Temperature: spriggler physics convert 75F K[/{note_style}]")
    console.print(f"  [{note_style}]             spriggler physics convert 24C F[/{note_style}]")
    console.print(f"  [{note_style}]Humidity:    spriggler physics convert 62%RH ah -t 75F[/{note_style}]")
    console.print(f"  [{note_style}]             spriggler physics convert 14.8ah %RH -t 80F[/{note_style}]")


@physics.command("info")
@click.argument("plugin_name")
@click.pass_context
def physics_info(ctx, plugin_name):
    """Show details of a specific physics plugin.

    \b
    Displays:
      • Derived and fundamental properties
      • Required co-properties
      • Full description
      • Example conversions at common operating points

    \b
    Examples:
      spriggler physics info rh_to_ah
    """
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    from spriggler.cli._style import console, C_BRAND, C_CMD, C_NOTE, C_ERROR
    from spriggler.physics.temperature import fahrenheit_to_kelvin

    reg = _load_plugins()

    # Find by name
    plugin = None
    for p in reg.list_plugins():
        if p.name == plugin_name:
            plugin = p
            break

    if plugin is None:
        available = [p.name for p in reg.list_plugins()]
        console.print(
            f"[{C_ERROR}]Unknown plugin: '{plugin_name}'[/{C_ERROR}]\n"
            f"  Available: {', '.join(available) if available else 'none'}"
        )
        raise SystemExit(1)

    body = Text()
    body.append("Name:          ", style="bold")
    body.append(f"{plugin.name}\n", style=C_CMD)
    body.append("Derived:       ", style="bold")
    body.append(f"{plugin.derived_property}\n")
    body.append("Fundamental:   ", style="bold")
    body.append(f"{plugin.fundamental_property}\n")
    body.append("Co-properties: ", style="bold")
    co = ", ".join(plugin.co_properties) if plugin.co_properties else "none"
    body.append(f"{co}\n\n")
    body.append(plugin.description)

    console.print(Panel(
        body,
        title=f"[{C_BRAND}]{plugin.name}[/{C_BRAND}]",
        border_style=C_BRAND,
        box=box.ROUNDED,
        padding=(1, 2),
    ))

    # Show example conversions at common grow conditions
    if plugin.derived_property == "humidity":
        console.print(f"\n  [{C_NOTE}]Example conversions at common grow conditions:[/{C_NOTE}]")
        examples = [
            (80.0, 80.0, "Seedling day target"),
            (75.0, 70.0, "Seedling night target"),
            (78.0, 62.0, "Typical reading"),
            (70.0, 50.0, "Cool/dry"),
            (85.0, 90.0, "Hot/humid"),
        ]
        for temp_f, rh, label in examples:
            temp_k = fahrenheit_to_kelvin(temp_f)
            ah = reg.to_fundamental("humidity", rh, temperature=temp_k)
            console.print(
                f"  [{C_NOTE}]{temp_f:5.1f}°F  {rh:5.1f}%RH  →  "
                f"{ah:6.2f} g/m³  ({label})[/{C_NOTE}]"
            )
