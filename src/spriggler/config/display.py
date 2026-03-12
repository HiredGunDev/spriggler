"""Config display — renders loaded config as Rich tables or raw formats.

Used by `spriggler config show`.  Each section has its own renderer
that knows how to present the data meaningfully.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from spriggler.cli._style import (
    C_BRAND, C_ACCENT, C_CMD, C_FLAG, C_NOTE, C_WARN, C_OK,
    styled_header,
)

console = Console()


# ── Section renderers ────────────────────────────────────────────

def render_meta(cfg: dict) -> None:
    """Render the [meta] and [units] sections."""
    meta = cfg.get("meta", {})
    units = cfg.get("units", {})

    t = Table(
        box=box.SIMPLE_HEAVY,
        show_header=False,
        padding=(0, 2),
    )
    t.add_column("Key", style="bold")
    t.add_column("Value")

    t.add_row("Name", meta.get("name", "—"))
    t.add_row("Version", meta.get("version", "—"))
    t.add_row("Config", str(cfg.get("_config_file", "—")))

    if units:
        unit_parts = []
        for prop, unit in units.items():
            unit_parts.append(f"{prop}: {unit}")
        t.add_row("Display units", ", ".join(unit_parts))

    defaults = cfg.get("sensor_defaults", {})
    if defaults:
        fresh = defaults.get("fresh_multiplier", "—")
        aging = defaults.get("aging_multiplier", "—")
        dead = defaults.get("dead_multiplier", "—")
        t.add_row(
            "Freshness multipliers",
            f"fresh {fresh}×  aging {aging}×  dead {dead}×",
        )

    console.print(Panel(t, title=f"[{C_BRAND}]Configuration[/{C_BRAND}]",
                        border_style=C_BRAND, box=box.ROUNDED))


def render_environments(cfg: dict) -> None:
    """Render the [environments] section."""
    envs = cfg.get("environments", {})
    if not envs:
        return

    t = Table(
        title="Environments",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Name", style=C_CMD)
    t.add_column("Description")
    t.add_column("Media")
    t.add_column("Controlled")

    for name, env in envs.items():
        controlled = env.get("controlled", True)
        ctrl_str = f"[{C_OK}]yes[/{C_OK}]" if controlled else f"[{C_NOTE}]no (measured only)[/{C_NOTE}]"
        t.add_row(
            name,
            env.get("description", "—"),
            ", ".join(env.get("media", [])),
            ctrl_str,
        )

    console.print(t)


def render_connections(cfg: dict) -> None:
    """Render the [connections] section."""
    conns = cfg.get("connections", {})
    if not conns:
        return

    t = Table(
        title="Connections",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Name", style=C_CMD)
    t.add_column("Endpoints")
    t.add_column("Medium")
    t.add_column("Transfer Device")

    for name, conn in conns.items():
        endpoints = conn.get("endpoints", [])
        ep_str = f"{endpoints[0]} ↔ {endpoints[1]}" if len(endpoints) == 2 else str(endpoints)
        t.add_row(
            name,
            ep_str,
            conn.get("medium", "—"),
            conn.get("transfer_device", f"[{C_NOTE}]passive only[/{C_NOTE}]"),
        )

    console.print(t)


def render_sensors(cfg: dict) -> None:
    """Render the [sensors] section."""
    sensors = cfg.get("sensors", {})
    if not sensors:
        return

    defaults = cfg.get("sensor_defaults", {})

    t = Table(
        title="Sensors",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Name", style=C_CMD)
    t.add_column("Driver")
    t.add_column("Environment")
    t.add_column("Reports")
    t.add_column("Delivery", justify="right")
    t.add_column("Address")

    for name, sensor in sensors.items():
        driver_cfg = sensor.get("driver_config", {})
        address = driver_cfg.get("address", "—")
        model = driver_cfg.get("model", "")
        if model:
            address = f"{address} ({model})"

        interval = sensor.get("delivery_interval_seconds")
        interval_str = f"{interval}s" if interval else "—"

        t.add_row(
            name,
            sensor.get("driver", "—"),
            sensor.get("environment", "—"),
            ", ".join(sensor.get("reports", [])),
            interval_str,
            address,
        )

    console.print(t)


def render_devices(cfg: dict) -> None:
    """Render the [devices] section."""
    devices = cfg.get("devices", {})
    if not devices:
        return

    t = Table(
        title="Devices",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Name", style=C_CMD)
    t.add_column("Type")
    t.add_column("Driver")
    t.add_column("Environment")
    t.add_column("Intended Properties")
    t.add_column("States")
    t.add_column("Circuit")

    for name, dev in devices.items():
        dev_type = dev.get("type", "—")
        type_style = C_OK if dev_type == "energy" else C_FLAG
        type_str = f"[{type_style}]{dev_type}[/{type_style}]"

        # Intended properties
        props = dev.get("intended_properties", {})
        if props:
            prop_parts = [f"{p} ({d})" for p, d in props.items()]
            props_str = ", ".join(prop_parts)
        elif dev.get("scheduled"):
            props_str = f"[{C_NOTE}]scheduled[/{C_NOTE}]"
        else:
            props_str = f"[{C_NOTE}]—[/{C_NOTE}]"

        # States
        states = dev.get("states")
        if states:
            states_str = " → ".join(states)
        else:
            states_str = "on/off"

        # Extra notes
        notes = []
        if dev.get("scheduled"):
            notes.append("scheduled")
        wattage = dev.get("wattage")
        if wattage:
            notes.append(f"{wattage}W")

        name_str = name
        if notes:
            name_str = f"{name} [{C_NOTE}]({', '.join(notes)})[/{C_NOTE}]"

        t.add_row(
            name_str,
            type_str,
            dev.get("driver", "—"),
            dev.get("environment", "—"),
            props_str,
            states_str,
            dev.get("circuit", "—"),
        )

    console.print(t)


def _format_target(value, unit: str) -> str:
    """Format a target value — handles both single value and min/max range."""
    if value is None:
        return "—"
    if isinstance(value, dict):
        # Legacy range format: {min: X, max: Y}
        return f"{value.get('min', '?')}–{value.get('max', '?')}{unit}"
    # Single target value
    return f"{value}{unit}"


def render_schedules(cfg: dict) -> None:
    """Render the [schedules] section."""
    schedules = cfg.get("schedules", {})
    if not schedules:
        return

    # Check for default bands to show alongside targets
    default_bands = cfg.get("safety", {}).get("default_bands", {})

    for env_name, sched in schedules.items():
        phases = sched.get("phases", [])
        if not phases:
            continue

        t = Table(
            title=f"Schedule: {env_name}",
            box=box.ROUNDED,
            title_style=f"bold {C_BRAND}",
            header_style="bold",
        )
        t.add_column("Phase", style=C_CMD)
        t.add_column("Time")
        t.add_column("Temperature Target")
        t.add_column("Humidity Target")
        t.add_column("Devices")

        for phase in phases:
            time_str = f"{phase.get('start', '?')} – {phase.get('end', '?')}"

            targets = phase.get("targets", {})
            temp = targets.get("temperature")
            hum = targets.get("humidity")

            # Show target with default band hint if it's a single value
            temp_band = default_bands.get("temperature")
            hum_band = default_bands.get("humidity")

            if isinstance(temp, (int, float)) and temp_band:
                temp_str = f"{temp}°F [{C_NOTE}](±{temp_band}° pre-cal)[/{C_NOTE}]"
            else:
                temp_str = _format_target(temp, "°F")

            if isinstance(hum, (int, float)) and hum_band:
                hum_str = f"{hum}% [{C_NOTE}](±{hum_band}% pre-cal)[/{C_NOTE}]"
            else:
                hum_str = _format_target(hum, "%")

            devs = phase.get("devices", {})
            dev_parts = [f"{d}: {s}" for d, s in devs.items()]
            devs_str = ", ".join(dev_parts) if dev_parts else "—"

            t.add_row(
                phase.get("name", "—"),
                time_str,
                temp_str,
                hum_str,
                devs_str,
            )

        console.print(t)


def render_circuits(cfg: dict) -> None:
    """Render the [circuits] section."""
    circuits = cfg.get("circuits", {})
    if not circuits:
        return

    t = Table(
        title="Circuits",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Name", style=C_CMD)
    t.add_column("Max Amps", justify="right")
    t.add_column("Voltage", justify="right")
    t.add_column("Max Watts", justify="right")
    t.add_column("Description")

    for name, circuit in circuits.items():
        amps = circuit.get("max_amps", 0)
        volts = circuit.get("voltage", 0)
        watts = amps * volts
        t.add_row(
            name,
            f"{amps}A",
            f"{volts}V",
            f"{watts}W",
            circuit.get("description", "—"),
        )

    console.print(t)


def render_safety(cfg: dict) -> None:
    """Render the [safety] section."""
    safety = cfg.get("safety", {})
    if not safety:
        return

    # Environment safety limits
    env_safety = safety.get("environments", {})
    if env_safety:
        t = Table(
            title="Safety Limits (Environments)",
            box=box.ROUNDED,
            title_style=f"bold {C_BRAND}",
            header_style="bold",
        )
        t.add_column("Environment", style=C_CMD)
        t.add_column("Property")
        t.add_column("Min", justify="right")
        t.add_column("Max", justify="right")
        t.add_column("Rate Limit")

        for env_name, env_data in env_safety.items():
            limits = env_data.get("limits", {})
            roc = env_data.get("rate_of_change", {})

            for prop, lim in limits.items():
                rate_key = f"{prop}_max_per_minute"
                rate_val = roc.get(rate_key)
                rate_str = f"{rate_val}°/min" if rate_val else "—"

                t.add_row(
                    env_name,
                    prop,
                    str(lim.get("absolute_min", "—")),
                    str(lim.get("absolute_max", "—")),
                    rate_str,
                )

        console.print(t)

    # Device safety
    dev_safety = safety.get("devices", {})
    if dev_safety:
        t = Table(
            title="Safety (Devices)",
            box=box.ROUNDED,
            title_style=f"bold {C_BRAND}",
            header_style="bold",
        )
        t.add_column("Device", style=C_CMD)
        t.add_column("Safe State")
        t.add_column("Coherence Window", justify="right")
        t.add_column("Max Runtime", justify="right")

        for dev_name, dev_data in dev_safety.items():
            safe = dev_data.get("safe_state", "—")
            safe_style = C_OK if safe == "off" else C_WARN
            safe_str = f"[{safe_style}]{safe}[/{safe_style}]"

            coherence = dev_data.get("coherence_window_seconds")
            coherence_str = f"{coherence}s" if coherence else "—"

            runtime = dev_data.get("max_continuous_runtime_minutes")
            if runtime:
                hours = runtime / 60
                runtime_str = f"{runtime}m ({hours:.0f}h)" if hours >= 1 else f"{runtime}m"
            else:
                runtime_str = f"[{C_NOTE}]unlimited[/{C_NOTE}]"

            t.add_row(dev_name, safe_str, coherence_str, runtime_str)

        console.print(t)

    # Global safety settings
    loop_interval = safety.get("safety_loop_interval_seconds")
    if loop_interval:
        console.print(f"  [{C_NOTE}]Safety loop interval: {loop_interval}s[/{C_NOTE}]")

    # Default bands (pre-calibration fallback)
    bands = safety.get("default_bands", {})
    if bands:
        parts = [f"{prop}: ±{val}" for prop, val in bands.items()]
        console.print(
            f"  [{C_NOTE}]Default bands (pre-calibration): "
            f"{', '.join(parts)}[/{C_NOTE}]"
        )


def render_power_monitoring(cfg: dict) -> None:
    """Render the [power_monitoring] section."""
    pm = cfg.get("power_monitoring", {})
    if not pm:
        return

    t = Table(
        title="Power Monitoring",
        box=box.ROUNDED,
        title_style=f"bold {C_BRAND}",
        header_style="bold",
    )
    t.add_column("Strip", style=C_CMD)
    t.add_column("Driver")
    t.add_column("Plug → Device Mapping")

    for strip_name, strip_data in pm.items():
        plug_map = strip_data.get("plug_map", {})
        mappings = [f"{plug} → {device}" for plug, device in plug_map.items()]
        t.add_row(
            strip_name,
            strip_data.get("driver", "—"),
            "\n".join(mappings) if mappings else "—",
        )

    console.print(t)


# ── Section dispatch ─────────────────────────────────────────────

SECTION_RENDERERS = {
    "environments": render_environments,
    "connections": render_connections,
    "sensors": render_sensors,
    "devices": render_devices,
    "schedules": render_schedules,
    "circuits": render_circuits,
    "safety": render_safety,
}

# Sections in display order
SECTION_ORDER = [
    "environments",
    "connections",
    "sensors",
    "devices",
    "schedules",
    "circuits",
    "safety",
]


def render_config(cfg: dict, section: str = "all", fmt: str = "table") -> None:
    """Render configuration to the console.

    Parameters
    ----------
    cfg : dict
        Loaded, resolved config from load_config().
    section : str
        Which section to show, or "all".
    fmt : str
        "table" for Rich tables, "toml" for raw TOML, "json" for JSON.
    """
    if fmt == "json":
        # Strip internal metadata
        output = {k: v for k, v in cfg.items() if not k.startswith("_")}
        console.print_json(json.dumps(output, default=str, indent=2))
        return

    if fmt == "toml":
        # Read and print the raw file — preserves comments
        config_file = cfg.get("_config_file")
        if config_file and config_file.is_file():
            console.print(config_file.read_text())
        else:
            console.print("[red]Config file not found[/red]")
        return

    # Table format
    if section == "all":
        render_meta(cfg)
        console.print()
        for sec in SECTION_ORDER:
            renderer = SECTION_RENDERERS.get(sec)
            if renderer:
                renderer(cfg)
                console.print()
        render_power_monitoring(cfg)
    else:
        renderer = SECTION_RENDERERS.get(section)
        if renderer:
            renderer(cfg)
        else:
            console.print(f"[red]Unknown section: {section}[/red]")
