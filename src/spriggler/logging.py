"""Structured logging for the Spriggler daemon.

Writes JSON-lines to a log file. Each line is a self-contained event
with a timestamp, event type, cycle number, and event-specific data.

The console formatter translates structured events into the
human-readable output Captain is used to seeing.

Log file: {config_dir}/spriggler.log (next to status.json)
Format: One JSON object per line, no pretty-printing.

Event types:
    daemon.start        Daemon startup with config summary
    daemon.stop         Clean shutdown
    config.reload       Config reload success or failure
    cycle.start         Beginning of a control cycle
    sensor.reading      Successful sensor read
    sensor.missed       Sensor returned no data
    safety.alert        Safety monitor alert
    safety.safe_mode    Environment entered safe mode
    safety.command      Safety override forced device state
    solver.result       Solver evaluation summary
    device.command      Device state change
    device.power        Device power consumption
    override.detected   Manual override detected
    override.expired    Manual override timer expired
    environment.summary Environment readings vs targets
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from spriggler.units import format_temp


log = logging.getLogger('spriggler')


class StructuredLogger:
    """Writes structured JSON-line events and optional console output.

    Usage:
        slog = StructuredLogger(log_path, display_unit='F')
        slog.emit('sensor.reading', cycle=1,
                  sensor_id='flower', temperature=295.37, ...)

    The log file gets every event as a JSON line.
    The console gets a human-readable translation.
    """

    def __init__(
            self,
            log_path: Path,
            display_unit: str = 'F',
            console: bool = True,
    ) -> None:
        self._log_path = log_path
        self._display_unit = display_unit
        self._console = console
        self._log_file = None
        self._open_log()

    def _open_log(self) -> None:
        """Open the log file for appending."""
        self._log_file = open(self._log_path, 'a')

    def close(self) -> None:
        """Flush and close the log file."""
        if self._log_file:
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None

    @property
    def display_unit(self) -> str:
        return self._display_unit

    @display_unit.setter
    def display_unit(self, unit: str) -> None:
        self._display_unit = unit

    def emit(self, event: str, **data) -> None:
        """Write one structured event.

        Args:
            event: Event type string (e.g., 'sensor.reading')
            **data: Event-specific key-value pairs. 'cycle' should
                    be included for all cycle-scoped events.
        """
        record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'event': event,
        }
        record.update(data)

        # Write JSON line
        line = json.dumps(record, default=str)
        if self._log_file:
            self._log_file.write(line + '\n')
            self._log_file.flush()

        # Console output
        if self._console:
            self._console_emit(event, record)

    def _fmt_temp(self, kelvin: float | None) -> str:
        """Format temperature for console display."""
        if kelvin is None:
            return "-- --"
        return format_temp(kelvin, self._display_unit)

    # ── Console formatters ───────────────────────────────────────────

    def _console_emit(self, event: str, data: dict) -> None:
        """Translate a structured event to console log output."""
        formatter = self._FORMATTERS.get(event)
        if formatter:
            formatter(self, data)
        else:
            # Fallback: log the event type and key data
            log.info("[%s] %s", event, {
                k: v for k, v in data.items()
                if k not in ('ts', 'event')
            })

    def _fmt_daemon_start(self, d: dict) -> None:
        log.info("Spriggler daemon starting")
        log.info("Config: %s", d.get('config_name', 'unnamed'))
        log.info("Environments: %s", d.get('environments', ''))
        log.info("Sensors: %d, Devices: %d",
                 d.get('sensor_count', 0), d.get('device_count', 0))
        log.info("Cycle interval: %ds", d.get('cycle_seconds', 0))
        log.info("Display unit: %s", d.get('display_unit', '?'))
        log.info("Status: %s", d.get('status_path', ''))
        log.info("Log: %s", d.get('log_path', ''))
        log.info("─" * 60)

    def _fmt_daemon_stop(self, d: dict) -> None:
        log.info("Spriggler daemon stopped (%d cycles)", d.get('cycles', 0))

    def _fmt_config_reload(self, d: dict) -> None:
        if d.get('success'):
            log.info("Config reloaded successfully")
        else:
            log.error("Config reload failed: %s", d.get('error', '?'))

    def _fmt_cycle_start(self, d: dict) -> None:
        ts = d.get('time', '')
        log.info("── Cycle %d ── %s ──", d.get('cycle', 0), ts)

    def _fmt_sensor_reading(self, d: dict) -> None:
        sensor_id = d.get('sensor_id', '?')
        env = d.get('environment', '?')
        temp_str = self._fmt_temp(d.get('temperature'))

        if env == 'ambient':
            log.info("  sensor %-20s  ambient  %s", sensor_id, temp_str)
        else:
            hum = d.get('humidity')
            hum_str = f"H:{hum:.1f}%" if hum is not None else ""
            log.info("  sensor %-20s  %-10s %s  %s",
                     sensor_id, env, temp_str, hum_str)

    def _fmt_sensor_missed(self, d: dict) -> None:
        log.warning("  sensor %-20s  NO DATA", d.get('sensor_id', '?'))

    def _fmt_safety_alert(self, d: dict) -> None:
        level = d.get('level', 'WARNING')
        log_level = {
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'CRITICAL': logging.CRITICAL,
            'EMERGENCY': logging.CRITICAL,
        }.get(level, logging.WARNING)
        log.log(log_level, "  SAFETY: [%s] %s", level, d.get('message', ''))

    def _fmt_safety_safe_mode(self, d: dict) -> None:
        envs = d.get('environments', [])
        log.warning("  SAFE MODE: %s", ', '.join(envs))

    def _fmt_safety_command(self, d: dict) -> None:
        log.warning("  SAFE CMD: %s → %s",
                    d.get('device_id', '?'), d.get('state', '?'))

    def _fmt_solver_result(self, d: dict) -> None:
        log.info("  SOLVER: evaluated %d/%d combinations, cost=%.4f",
                 d.get('feasible', 0), d.get('total', 0),
                 d.get('cost', 0))

    def _fmt_device_command(self, d: dict) -> None:
        log.info("  CMD: %-20s %s → %s",
                 d.get('device_id', '?'),
                 d.get('old_state', '?'),
                 d.get('new_state', '?'))

    def _fmt_device_power(self, d: dict) -> None:
        log.info("  PWR: %-20s %.1f W",
                 d.get('device_id', '?'), d.get('watts', 0))

    def _fmt_override_detected(self, d: dict) -> None:
        log.info("  OVERRIDE: %s manually set to '%s', "
                 "respecting for %d min",
                 d.get('device_id', '?'),
                 d.get('actual_state', '?'),
                 d.get('hold_minutes', 0))

    def _fmt_override_expired(self, d: dict) -> None:
        log.info("  OVERRIDE EXPIRED: %s, solver resuming control",
                 d.get('device_id', '?'))

    def _fmt_override_mismatch(self, d: dict) -> None:
        log.warning("  MISMATCH: %s is '%s', expected '%s'. Correcting.",
                    d.get('device_id', '?'),
                    d.get('actual_state', '?'),
                    d.get('commanded_state', '?'))

    def _fmt_environment_summary(self, d: dict) -> None:
        env_id = d.get('environment', '?')
        temp_str = self._fmt_temp(d.get('temperature'))
        min_str = self._fmt_temp(d.get('target_min'))
        max_str = self._fmt_temp(d.get('target_max'))
        log.info("  ENV %-12s  %s  [%s – %s]",
                 env_id, temp_str, min_str, max_str)

    # Formatter dispatch table
    _FORMATTERS = {
        'daemon.start': _fmt_daemon_start,
        'daemon.stop': _fmt_daemon_stop,
        'config.reload': _fmt_config_reload,
        'cycle.start': _fmt_cycle_start,
        'sensor.reading': _fmt_sensor_reading,
        'sensor.missed': _fmt_sensor_missed,
        'safety.alert': _fmt_safety_alert,
        'safety.safe_mode': _fmt_safety_safe_mode,
        'safety.command': _fmt_safety_command,
        'solver.result': _fmt_solver_result,
        'device.command': _fmt_device_command,
        'device.power': _fmt_device_power,
        'override.detected': _fmt_override_detected,
        'override.expired': _fmt_override_expired,
        'override.mismatch': _fmt_override_mismatch,
        'environment.summary': _fmt_environment_summary,
    }