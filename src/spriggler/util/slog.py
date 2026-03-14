"""Structured JSON logging for Spriggler.

Every significant event is written as a single JSON line to the
log file.  Events include:

  - daemon.start / daemon.stop
  - cycle (sensor readings, decisions, commands, state)
  - device.command / device.failed
  - sensor.stale / sensor.dead
  - safety (limit violations, safe mode transitions)
  - calibration events

The log file is append-only.  Each line is a complete JSON object
with a timestamp and event type.  External tools can parse, filter,
and analyze.

Usage:
    slog = StructuredLogger(home_path)
    slog.log("cycle", cycle=42, temperature=299.8, ...)
    slog.log("device.command", device="heater", state="on", reason="...")
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("spriggler.slog")


class StructuredLogger:
    """Append-only structured JSON logger with daily rotation.

    Log files are named by date: spriggler.2026-03-14.log
    On each write, checks if the date has changed and rotates.
    Old files beyond retention_days are deleted.
    """

    DEFAULT_RETENTION_DAYS = 180

    def __init__(self, home: Path,
                 retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self._log_dir = home / "log"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._retention_days = retention_days
        self._file = None
        self._current_date: str = ""
        self._rotate()
        self._cleanup_old_logs()

    def _date_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _log_path(self, date_str: str) -> Path:
        return self._log_dir / f"spriggler.{date_str}.log"

    def _rotate(self) -> None:
        """Open today's log file. Close yesterday's if open."""
        today = self._date_str()
        if today == self._current_date and self._file:
            return
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
        self._current_date = today
        try:
            self._file = open(
                self._log_path(today), "a", buffering=1)
        except Exception as e:
            log.error("Cannot open log file: %s", e)
            self._file = None

    def _cleanup_old_logs(self) -> None:
        """Delete log files older than retention_days."""
        import glob
        cutoff = datetime.now().timestamp() - (
            self._retention_days * 86400)
        for path in self._log_dir.glob("spriggler.*.log"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    log.info("Deleted old log: %s", path.name)
            except Exception:
                pass

    def log(self, event: str, **kwargs: Any) -> None:
        """Write one structured log entry."""
        self._rotate()  # Check if date changed

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        entry.update(kwargs)

        if self._file:
            try:
                self._file.write(json.dumps(entry, default=str) + "\n")
            except Exception as e:
                log.error("Log write failed: %s", e)

    def log_cycle(self, state) -> None:
        """Log a complete control cycle from ControllerState.

        Captures everything in one entry: sensor readings, device
        states, property values, decisions.
        """
        from spriggler.physics.temperature import kelvin_to_fahrenheit

        # Environment properties
        props = {}
        for name, ps in state.properties.items():
            p = {"current": ps.current, "target": ps.target,
                 "delta": ps.delta, "action": ps.action}
            if name == "temperature" and ps.current:
                p["current_f"] = round(kelvin_to_fahrenheit(ps.current), 1)
                if ps.target:
                    p["target_f"] = round(kelvin_to_fahrenheit(ps.target), 1)
                    p["delta_f"] = round(ps.delta * 9/5, 1)
            props[name] = p

        # Ambient
        ambient = {}
        for k, v in state.ambient.items():
            ambient[k] = round(v, 2)
            if k == "temperature" and v:
                ambient["temperature_f"] = round(kelvin_to_fahrenheit(v), 1)

        # Sensors
        sensors = {}
        for name, ss in state.sensors.items():
            sensors[name] = {
                "freshness": ss.freshness.value,
                "age": round(ss.age, 1),
                "battery": ss.battery,
                "delivery_avg": round(ss.delivery_avg, 1),
            }

        # Devices
        devices = {}
        for name, ds in state.devices.items():
            devices[name] = {
                "state": ds.commanded_state,
                "since": round(ds.commanded_at, 1) if ds.commanded_at else None,
                "verified": ds.verified,
                "power": ds.power,
                "runtime": round(ds.continuous_runtime, 1),
                "reason": ds.reason,
            }

        # Passive
        passive = {}
        for prop in ("temperature", "absolute_humidity"):
            rate = state.passive_loss.get(prop)
            diff = state.passive_diff.get(prop)
            if rate is not None:
                passive[prop] = {
                    "rate": round(rate, 6),
                    "diff": round(diff, 2) if diff else None,
                }

        self.log(
            "cycle",
            cycle=state.cycle_count,
            uptime=round(state.uptime, 1),
            cycle_time_ms=round(state.cycle_time * 1000, 1),
            schedule=state.schedule_period,
            properties=props,
            ambient=ambient,
            sensors=sensors,
            devices=devices,
            passive=passive,
        )

    def log_command(self, device: str, old_state: str,
                    new_state: str, reason: str) -> None:
        """Log a device command."""
        self.log("device.command",
                 device=device,
                 old_state=old_state,
                 new_state=new_state,
                 reason=reason)

    def log_sensor_issue(self, sensor: str, freshness: str,
                         age: float) -> None:
        """Log a sensor freshness issue."""
        self.log("sensor.issue",
                 sensor=sensor,
                 freshness=freshness,
                 age=round(age, 1))

    def log_start(self, environment: str, device_count: int,
                  sensor_count: int, dry_run: bool = False) -> None:
        """Log daemon start."""
        self.log("daemon.start",
                 environment=environment,
                 devices=device_count,
                 sensors=sensor_count,
                 dry_run=dry_run)

    def log_stop(self, cycles: int) -> None:
        """Log daemon stop."""
        self.log("daemon.stop", cycles=cycles)

    def log_calibration_event(self, event: str, **kwargs) -> None:
        """Log a calibration event."""
        self.log(f"calibration.{event}", **kwargs)

    def close(self) -> None:
        """Close the log file."""
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
