"""Dashboard display — Rich Live terminal UI for foreground mode.

Renders the ControllerState as a compact, color-coded dashboard
that refreshes in place.  Adapts layout to terminal width:
wide terminals get side-by-side panels, narrow terminals stack.
"""

from __future__ import annotations

import time
from datetime import datetime

from rich.console import Console, Group
from rich.columns import Columns
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich import box

from spriggler.cli._style import (
    C_BRAND, C_CMD, C_OK, C_WARN, C_ERROR, C_NOTE, C_STALE, C_DEAD,
)
from spriggler.sensors.freshness import Freshness

# Width threshold for side-by-side layout
WIDE_THRESHOLD = 100


def _format_uptime(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    elif m > 0:
        return f"{m}m {s:02d}s"
    else:
        return f"{s}s"


def _format_since(timestamp: float) -> str:
    if timestamp <= 0:
        return "—"
    age = time.time() - timestamp
    if age < 60:
        return f"{age:.0f}s ago"
    elif age < 3600:
        return f"{age/60:.0f}m ago"
    else:
        return f"{age/3600:.1f}h ago"


def _freshness_style(f: Freshness) -> str:
    return {
        Freshness.FRESH: C_OK,
        Freshness.AGING: C_WARN,
        Freshness.STALE: C_STALE,
        Freshness.DEAD: C_DEAD,
    }.get(f, C_NOTE)


def _level_style(level: str) -> str:
    return {
        "info": C_NOTE,
        "warn": C_WARN,
        "error": C_ERROR,
    }.get(level, C_NOTE)


def render_dashboard(state, console_width: int = 120) -> Group:
    """Render the full dashboard as a Rich Group.

    Parameters
    ----------
    state : ControllerState
        Current controller state.
    console_width : int
        Terminal width for layout decisions.
    """
    from spriggler.physics.temperature import kelvin_to_fahrenheit
    from spriggler.calibrate.engine import _rh_from_temp_and_ah

    wide = console_width >= WIDE_THRESHOLD

    # ── Header ───────────────────────────────────────────────
    header = Text()
    header.append("Spriggler", style=f"bold {C_BRAND}")
    header.append(f" — {state.environment}    ", style="bold")
    header.append(f"uptime: {_format_uptime(state.uptime)}  ", style=C_NOTE)
    header.append(f"cycle: {state.cycle_time*1000:.0f}ms  ", style=C_NOTE)
    header.append(state.schedule_desc, style=C_CMD)

    # ── Left panel: Environment + Devices ────────────────────

    # Environment table
    env_table = Table(box=box.SIMPLE, show_header=True,
                      header_style="bold", padding=(0, 1))
    env_table.add_column("Property", style=C_CMD)
    env_table.add_column("Target", justify="right")
    env_table.add_column("Current", justify="right")
    env_table.add_column("Δ", justify="right")
    env_table.add_column("Action")

    for prop, ps in state.properties.items():
        if prop == "temperature":
            current_f = kelvin_to_fahrenheit(ps.current) if ps.current else 0
            target_f = kelvin_to_fahrenheit(ps.target) if ps.target else 0
            delta_f = ps.delta * 9/5 if ps.delta else 0
            delta_style = C_OK if abs(delta_f) < 2 else (C_WARN if abs(delta_f) < 5 else C_ERROR)
            action_style = C_OK if ps.action == "at target" else C_WARN

            target_str = f"{target_f:.1f}°F" if ps.target else "—"
            env_table.add_row(
                "temperature",
                target_str,
                f"{current_f:.1f}°F",
                f"[{delta_style}]{delta_f:+.1f}°F[/{delta_style}]",
                f"[{action_style}]{ps.action}[/{action_style}]",
            )
        elif prop == "absolute_humidity":
            current_ah = ps.current or 0
            target_ah = ps.target or 0
            delta_ah = ps.delta or 0
            delta_style = C_OK if abs(delta_ah) < 1 else (C_WARN if abs(delta_ah) < 3 else C_ERROR)
            action_style = C_OK if ps.action == "at target" else C_WARN

            # Show %RH in parentheses
            current_temp = 0
            for p2 in state.properties.values():
                if p2.name == "temperature":
                    current_temp = p2.current
            rh_str = ""
            if current_temp and current_ah:
                rh = _rh_from_temp_and_ah(current_temp, current_ah) * 100
                rh_str = f"({rh:.0f}%)"

            target_rh_str = ""
            if current_temp and target_ah:
                trh = _rh_from_temp_and_ah(current_temp, target_ah) * 100
                target_rh_str = f"({trh:.0f}%)"

            env_table.add_row(
                "humidity",
                f"{target_ah:.1f}{target_rh_str}" if ps.target else "—",
                f"{current_ah:.1f} {rh_str}",
                f"[{delta_style}]{delta_ah:+.1f}[/{delta_style}]",
                f"[{action_style}]{ps.action}[/{action_style}]",
            )

    # Ambient row
    amb_temp = state.ambient.get("temperature")
    amb_ah = state.ambient.get("absolute_humidity")
    if amb_temp or amb_ah:
        t_str = f"{kelvin_to_fahrenheit(amb_temp):.1f}°F" if amb_temp else "—"
        h_str = f"{amb_ah:.1f} g/m³" if amb_ah else "—"
        env_table.add_row(
            f"[{C_NOTE}]ambient[/{C_NOTE}]",
            "—", f"{t_str} / {h_str}", "", "",
        )

    # Device table
    dev_table = Table(box=box.SIMPLE, show_header=True,
                      header_style="bold", padding=(0, 1))
    dev_table.add_column("Device", style=C_CMD)
    dev_table.add_column("State")
    dev_table.add_column("Since")
    dev_table.add_column("Verified")
    dev_table.add_column("Power", justify="right")

    for name, ds in state.devices.items():
        state_style = C_OK if ds.commanded_state != "off" else C_NOTE
        verified_style = {
            "rising": C_OK, "falling": C_OK, "stable": C_OK,
            "pending": C_WARN, "failed": C_ERROR,
            "dry-run": C_CMD,
        }.get(ds.verified, C_NOTE)

        power_str = f"{ds.power:.0f}W" if ds.power is not None else "—"

        # Short name
        short = name.replace(f"{state.environment}_", "")

        dev_table.add_row(
            short,
            f"[{state_style}]{ds.commanded_state}[/{state_style}]",
            _format_since(ds.commanded_at) if ds.commanded_state != "off" else "—",
            f"[{verified_style}]{ds.verified}[/{verified_style}]",
            power_str,
        )

    left_panel = Group(env_table, Text(""), dev_table)

    # ── Right panel: Sensors + Passive ───────────────────────

    # Sensor table
    sen_table = Table(box=box.SIMPLE, show_header=True,
                      header_style="bold", padding=(0, 1),
                      title="Sensors", title_style=f"bold {C_NOTE}")
    sen_table.add_column("Sensor", style=C_CMD)
    sen_table.add_column("Fresh")
    sen_table.add_column("Age", justify="right")
    sen_table.add_column("Bat", justify="right")

    for name, ss in state.sensors.items():
        f_style = _freshness_style(ss.freshness)
        short = name.replace(f"{state.environment}_", "").replace("_sensor", "")
        bat_str = f"{ss.battery:.0f}%" if ss.battery else "—"
        bat_style = C_OK if (ss.battery or 0) > 20 else C_WARN

        sen_table.add_row(
            short,
            f"[{f_style}]{ss.freshness.value}[/{f_style}]",
            f"{ss.age:.1f}s",
            f"[{bat_style}]{bat_str}[/{bat_style}]",
        )

    # Passive loss table
    pass_table = Table(box=box.SIMPLE, show_header=True,
                       header_style="bold", padding=(0, 1),
                       title="Passive Loss", title_style=f"bold {C_NOTE}")
    pass_table.add_column("Property")
    pass_table.add_column("Rate", justify="right")
    pass_table.add_column("Diff", justify="right")

    for prop in ("temperature", "absolute_humidity"):
        rate = state.passive_loss.get(prop, 0)
        diff = state.passive_diff.get(prop, 0)
        if prop == "temperature":
            rate_str = f"{rate * 9/5 * 60:+.2f}°F/min"
            diff_str = f"{diff * 9/5:.1f}°F"
        else:
            rate_str = f"{rate * 60:+.3f} g/m³/min"
            diff_str = f"{diff:.1f} g/m³"
        pass_table.add_row(
            prop.replace("absolute_", ""),
            rate_str, diff_str,
        )

    right_panel = Group(sen_table, Text(""), pass_table)

    # ── Messages ─────────────────────────────────────────────
    msg_lines = Text()
    recent = state.messages[-8:]  # Last 8 messages
    for m in recent:
        ts = datetime.fromtimestamp(m.timestamp).strftime("%H:%M:%S")
        style = _level_style(m.level)
        msg_lines.append(f" {ts}  ", style=C_NOTE)
        msg_lines.append(f"{m.text}\n", style=style)

    # ── Assemble ─────────────────────────────────────────────
    if wide:
        main = Columns([left_panel, right_panel], padding=(0, 3))
    else:
        main = Group(left_panel, Text(""), right_panel)

    separator = Text("─" * min(console_width, 90), style=C_NOTE)

    return Group(
        header,
        Text(""),
        main,
        separator,
        msg_lines,
    )
