"""Shared CLI styling — colors, banner, stub formatters.

Uses Rich for terminal styling.  All color/style decisions live here
so the command modules stay clean.
"""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()

# ── Color palette ──────────────────────────────────────────────────
# Semantic color names so we can adjust the palette in one place.

C_BRAND      = "green"         # Spriggler brand / headers
C_ACCENT     = "bright_green"  # Highlights
C_CMD        = "cyan"          # Command names in help text
C_FLAG       = "yellow"        # Flags / options
C_PHASE      = "bright_cyan"   # Phase labels
C_NOTE       = "dim"           # Explanatory notes
C_WARN       = "yellow"        # Warnings
C_ERROR      = "bold red"      # Errors
C_OK         = "bold green"    # Success / healthy
C_STALE      = "bold yellow"   # Degraded / aging
C_DEAD       = "bold red"      # Failed / dead


BANNER = Text.from_markup(
    f"[{C_BRAND} bold]"
    "  ____             _             _\n"
    " / ___| _ __  _ __(_) __ _  __ _| | ___ _ __\n"
    " \\___ \\| '_ \\| '__| |/ _` |/ _` | |/ _ \\ '__|\n"
    "  ___) | |_) | |  | | (_| | (_| | |  __/ |\n"
    " |____/| .__/|_|  |_|\\__, |\\__, |_|\\___|_|\n"
    "       |_|           |___/ |___/\n"
    f"[/{C_BRAND} bold]"
    f"  [{C_NOTE}]v0.5 — physics-informed environmental control[/{C_NOTE}]"
)


def styled_header(text: str) -> None:
    """Print a styled section header."""
    console.print(f"\n[{C_BRAND} bold]── {text} ──[/{C_BRAND} bold]")


def in_development(
    command: str,
    phase: str,
    summary: str,
    notes: str | None = None,
    depends_on: list[str] | None = None,
    salvage_from_v04: list[str] | None = None,
) -> None:
    """Print a rich 'in development' placeholder for a stubbed command.

    Parameters
    ----------
    command : str
        The full command path, e.g. "spriggler calibrate run".
    phase : str
        Implementation phase, e.g. "Phase 0", "Phase 1".
    summary : str
        Plain-English description of what this command will do.
    notes : str, optional
        Implementation notes, design decisions, gotchas.
    depends_on : list[str], optional
        Other commands or subsystems this depends on.
    salvage_from_v04 : list[str], optional
        v0.4 files worth carrying forward for this command.
    """
    body = Text()

    body.append("Command:  ", style="bold")
    body.append(f"{command}\n", style=C_CMD)
    body.append("Phase:    ", style="bold")
    body.append(f"{phase}\n", style=C_PHASE)
    body.append("Status:   ", style="bold")
    body.append("In Development\n", style=C_WARN)
    body.append("\n")
    body.append(summary)

    if notes:
        body.append("\n\n")
        body.append("Design notes:\n", style="bold")
        body.append(notes, style=C_NOTE)

    if depends_on:
        body.append("\n\n")
        body.append("Depends on:\n", style="bold")
        for dep in depends_on:
            body.append(f"  • {dep}\n", style=C_NOTE)

    if salvage_from_v04:
        body.append("\n")
        body.append("v0.4 code to salvage:\n", style="bold")
        for path in salvage_from_v04:
            body.append(f"  • {path}\n", style=C_NOTE)

    console.print()
    console.print(Panel(
        body,
        title=f"[{C_WARN}]🚧  In Development[/{C_WARN}]",
        border_style=C_WARN,
        box=box.ROUNDED,
        padding=(1, 2),
    ))
