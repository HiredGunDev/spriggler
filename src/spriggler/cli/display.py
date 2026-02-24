"""Live terminal dashboard for Spriggler.

Reads ~/.spriggler/status.json on a timer and paints a curses
display showing environments, sensors, devices, and solver state.

Controls:
    q / Ctrl-C    Quit
    f / F         Toggle Fahrenheit/Celsius
    r             Force refresh

The display is read-only. It never writes to the state directory
or communicates with the daemon.
"""

import curses
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _kelvin_to_f(k: float) -> float:
    return (k - 273.15) * 9 / 5 + 32


def _kelvin_to_c(k: float) -> float:
    return k - 273.15


def _fmt_temp(kelvin: float | None, unit: str) -> str:
    if kelvin is None:
        return "-- --"
    if unit == 'C':
        return f"{_kelvin_to_c(kelvin):.1f}°C"
    return f"{_kelvin_to_f(kelvin):.1f}°F"


def _fmt_age(ts_iso: str | None) -> str:
    """Format how long ago a timestamp was."""
    if not ts_iso:
        return "never"
    try:
        ts = datetime.fromisoformat(ts_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts
        secs = int(age.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s ago"
        return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
    except (ValueError, TypeError):
        return "?"


def _load_status(status_path: Path) -> dict | None:
    """Load status.json, returning None on any error."""
    try:
        with open(status_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _safe_addstr(win, y: int, x: int, text: str, attr=0) -> None:
    """addstr that silently ignores writes outside the window."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0:
        return
    # Truncate to fit
    max_len = w - x
    if max_len <= 0:
        return
    try:
        win.addstr(y, x, text[:max_len], attr)
    except curses.error:
        pass  # Bottom-right corner write raises on some terminals


def _draw(stdscr, status: dict | None, unit: str, state_dir: Path) -> None:
    """Draw the full dashboard."""
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    # Colors
    TITLE = curses.color_pair(1) | curses.A_BOLD
    HEADER = curses.color_pair(2) | curses.A_BOLD
    GOOD = curses.color_pair(3)
    WARN = curses.color_pair(4)
    BAD = curses.color_pair(5) | curses.A_BOLD
    DIM = curses.color_pair(6)
    BOLD = curses.A_BOLD

    row = 0

    # ── Title bar ────────────────────────────────────────────────────
    title = " SPRIGGLER "
    bar = f"{'─' * 3}{title}{'─' * max(0, width - len(title) - 3)}"
    _safe_addstr(stdscr, row, 0, bar[:width], TITLE)
    row += 1

    if status is None:
        _safe_addstr(stdscr, row + 1, 2,
                     f"Waiting for daemon... ({state_dir}/status.json)",
                     WARN)
        _safe_addstr(stdscr, row + 3, 2,
                     "Is spriggler-daemon running?", DIM)
        _safe_addstr(stdscr, height - 1, 0,
                     " q:quit  f:°F/°C  r:refresh ", DIM)
        stdscr.refresh()
        return

    # ── Status line ──────────────────────────────────────────────────
    cycle = status.get('cycle', '?')
    ts = status.get('timestamp', '')
    age = _fmt_age(ts)
    config_err = status.get('config_error')
    is_running = status.get('running', True)

    # Three-state daemon health: running, stopped, crashed/hung
    if not is_running:
        daemon_str = "STOPPED"
        daemon_attr = BAD
    else:
        try:
            ts_dt = datetime.fromisoformat(ts)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            age_secs = (datetime.now(timezone.utc) - ts_dt).total_seconds()
            if age_secs > 300:
                daemon_str = "NOT RESPONDING"
                daemon_attr = BAD
            elif age_secs > 120:
                daemon_str = "STALE"
                daemon_attr = WARN
            else:
                daemon_str = "RUNNING"
                daemon_attr = GOOD
        except (ValueError, TypeError):
            daemon_str = "RUNNING"
            daemon_attr = GOOD

    status_text = f"  Cycle {cycle}  │  Updated {age}  │  Unit: {unit}  │  "
    _safe_addstr(stdscr, row, 0, status_text, DIM)
    _safe_addstr(stdscr, row, len(status_text), daemon_str, daemon_attr)

    if config_err:
        row += 1
        _safe_addstr(stdscr, row, 2, f"CONFIG ERROR: {config_err}", BAD)
    row += 2

    # ── Environments ─────────────────────────────────────────────────
    environments = status.get('environments', {})

    if environments:
        _safe_addstr(stdscr, row, 1, "ENVIRONMENTS", HEADER)
        row += 1

        # Header
        hdr = f"  {'Name':<14} {'Temp':>9} {'Target':>19} {'Humidity':>9} {'Target':>15} {'Status':>10}"
        _safe_addstr(stdscr, row, 0, hdr[:width], DIM)
        row += 1

        for env_id, env_data in environments.items():
            readings = env_data.get('readings', {})
            targets = env_data.get('targets', {})
            safe_mode = env_data.get('safe_mode', False)

            # Temperature
            temp_k = readings.get('temperature')
            temp_str = _fmt_temp(temp_k, unit)

            temp_targets = targets.get('temperature', {})
            tgt_min = temp_targets.get('min')
            tgt_max = temp_targets.get('max')
            if tgt_min is not None and tgt_max is not None:
                tgt_str = f"[{_fmt_temp(tgt_min, unit)} – {_fmt_temp(tgt_max, unit)}]"
            else:
                tgt_str = ""

            # Check if in range
            temp_attr = GOOD
            if temp_k is not None and tgt_min is not None and tgt_max is not None:
                if temp_k < tgt_min or temp_k > tgt_max:
                    temp_attr = WARN

            # Humidity
            hum = readings.get('humidity')
            hum_str = f"{hum:.1f}%" if hum is not None else "-- --"

            hum_targets = targets.get('humidity', {})
            hum_min = hum_targets.get('min')
            hum_max = hum_targets.get('max')
            if hum_min is not None and hum_max is not None:
                hum_tgt_str = f"[{hum_min:.0f}% – {hum_max:.0f}%]"
            else:
                hum_tgt_str = ""

            hum_attr = GOOD
            if hum is not None and hum_min is not None and hum_max is not None:
                if hum < hum_min or hum > hum_max:
                    hum_attr = WARN

            # Status
            if safe_mode:
                status_str = "SAFE MODE"
                status_attr = BAD
            elif temp_k is None:
                status_str = "NO DATA"
                status_attr = WARN
            else:
                status_str = "OK"
                status_attr = GOOD

            _safe_addstr(stdscr, row, 1, f"  {env_id:<14}", BOLD)
            _safe_addstr(stdscr, row, 16, f"{temp_str:>9}", temp_attr)
            _safe_addstr(stdscr, row, 26, f"{tgt_str:>19}", DIM)
            _safe_addstr(stdscr, row, 46, f"{hum_str:>9}", hum_attr)
            _safe_addstr(stdscr, row, 56, f"{hum_tgt_str:>15}", DIM)
            _safe_addstr(stdscr, row, 72, f"{status_str:>10}", status_attr)
            row += 1

        row += 1

    # ── Ambient ──────────────────────────────────────────────────────
    ambient = status.get('ambient', {})
    if ambient:
        temp_k = ambient.get('temperature')
        hum = ambient.get('humidity')
        _safe_addstr(stdscr, row, 1, "AMBIENT", HEADER)
        parts = []
        if temp_k is not None:
            parts.append(_fmt_temp(temp_k, unit))
        if hum is not None:
            parts.append(f"H:{hum:.1f}%")
        _safe_addstr(stdscr, row, 16, "  ".join(parts), DIM)
        row += 2

    # ── Sensors ──────────────────────────────────────────────────────
    sensors = status.get('sensors', {})

    if sensors:
        _safe_addstr(stdscr, row, 1, "SENSORS", HEADER)
        row += 1

        hdr = f"  {'Name':<22} {'Stale':>6} {'Missed':>7} {'Battery':>8} {'RSSI':>6}"
        _safe_addstr(stdscr, row, 0, hdr[:width], DIM)
        row += 1

        for sensor_id, sensor_data in sensors.items():
            stale = sensor_data.get('stale', False)
            missed = sensor_data.get('missed_polls', 0)
            battery = sensor_data.get('battery')
            rssi = sensor_data.get('signal_strength')

            name_attr = BAD if stale else BOLD
            _safe_addstr(stdscr, row, 1, f"  {sensor_id:<22}", name_attr)

            stale_str = "STALE" if stale else "ok"
            stale_attr = BAD if stale else GOOD
            _safe_addstr(stdscr, row, 24, f"{stale_str:>6}", stale_attr)

            missed_attr = WARN if missed > 0 else DIM
            _safe_addstr(stdscr, row, 31, f"{missed:>7}", missed_attr)

            if battery is not None:
                batt_str = f"{battery:.0f}%"
                batt_attr = WARN if battery < 20 else DIM
            else:
                batt_str = "--"
                batt_attr = DIM
            _safe_addstr(stdscr, row, 39, f"{batt_str:>8}", batt_attr)

            if rssi is not None:
                rssi_str = f"{rssi:.0f}"
                rssi_attr = WARN if rssi < -85 else DIM
            else:
                rssi_str = "--"
                rssi_attr = DIM
            _safe_addstr(stdscr, row, 48, f"{rssi_str:>6}", rssi_attr)
            row += 1

        row += 1

    # ── Devices ──────────────────────────────────────────────────────
    devices = status.get('devices', {})

    if devices:
        _safe_addstr(stdscr, row, 1, "DEVICES", HEADER)
        row += 1

        hdr = f"  {'Name':<22} {'State':>8} {'Power':>9} {'Locked':>8} {'Override':>10}"
        _safe_addstr(stdscr, row, 0, hdr[:width], DIM)
        row += 1

        for dev_id, dev_data in devices.items():
            state = dev_data.get('state', '?')
            power = dev_data.get('power_watts')
            locked = dev_data.get('locked_out', False)
            override = dev_data.get('manual_override', False)
            runtime = dev_data.get('runtime_seconds')

            _safe_addstr(stdscr, row, 1, f"  {dev_id:<22}", BOLD)

            state_attr = GOOD if state != 'off' else DIM
            _safe_addstr(stdscr, row, 24, f"{state:>8}", state_attr)

            if power is not None and power > 0:
                pwr_str = f"{power:.0f} W"
            else:
                pwr_str = "-- W"
            _safe_addstr(stdscr, row, 33, f"{pwr_str:>9}", DIM)

            locked_str = "LOCKED" if locked else "--"
            locked_attr = BAD if locked else DIM
            _safe_addstr(stdscr, row, 43, f"{locked_str:>8}", locked_attr)

            ovr_str = "MANUAL" if override else "--"
            ovr_attr = WARN if override else DIM
            _safe_addstr(stdscr, row, 52, f"{ovr_str:>10}", ovr_attr)

            if runtime is not None and runtime > 0:
                mins = int(runtime) // 60
                secs = int(runtime) % 60
                _safe_addstr(stdscr, row, 64, f"({mins}m{secs:02d}s)", DIM)

            row += 1

        row += 1

    # ── Solver ───────────────────────────────────────────────────────
    solver = status.get('solver', {})

    if solver:
        _safe_addstr(stdscr, row, 1, "SOLVER", HEADER)
        cost = solver.get('last_cost', 0)
        feasible = solver.get('feasible_combinations', 0)
        total = solver.get('total_combinations', 0)
        _safe_addstr(stdscr, row, 16,
                     f"cost={cost:.4f}  ({feasible}/{total} feasible)",
                     DIM)
        row += 1

    # ── Footer ───────────────────────────────────────────────────────
    footer = " q:quit  f:°F/°C  r:refresh "
    _safe_addstr(stdscr, height - 1, 0, footer, DIM)

    stdscr.refresh()


def _main_loop(stdscr, state_dir: Path, interval: float) -> None:
    """Curses main loop: load status, draw, handle input, repeat."""
    # Setup
    curses.curs_set(0)       # Hide cursor
    stdscr.timeout(0)        # Non-blocking getch
    curses.use_default_colors()

    # Define color pairs (fg, bg) — -1 means default
    curses.init_pair(1, curses.COLOR_CYAN, -1)      # TITLE
    curses.init_pair(2, curses.COLOR_CYAN, -1)       # HEADER
    curses.init_pair(3, curses.COLOR_GREEN, -1)      # GOOD
    curses.init_pair(4, curses.COLOR_YELLOW, -1)     # WARN
    curses.init_pair(5, curses.COLOR_RED, -1)        # BAD
    curses.init_pair(6, curses.COLOR_WHITE, -1)      # DIM

    status_path = state_dir / 'status.json'
    unit = 'F'

    last_draw = 0.0

    while True:
        now = time.time()

        # Refresh on interval
        if now - last_draw >= interval:
            status = _load_status(status_path)
            _draw(stdscr, status, unit, state_dir)
            last_draw = now

        # Handle input
        key = stdscr.getch()
        if key == ord('q') or key == ord('Q') or key == 27:  # q, Q, Esc
            break
        elif key == ord('f') or key == ord('F'):
            unit = 'C' if unit == 'F' else 'F'
            last_draw = 0  # Force redraw
        elif key == ord('r') or key == ord('R'):
            last_draw = 0  # Force redraw
        elif key == curses.KEY_RESIZE:
            stdscr.clear()
            last_draw = 0  # Force redraw

        # Brief sleep to avoid CPU spin
        time.sleep(0.05)


def run_display(state_dir: Path, interval: float = 2.0) -> None:
    """Entry point for 'spriggler display'."""
    try:
        curses.wrapper(lambda stdscr: _main_loop(stdscr, state_dir, interval))
    except KeyboardInterrupt:
        pass