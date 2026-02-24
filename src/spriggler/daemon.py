"""Spriggler daemon - the main control loop.

Connects all components: config, drivers, safety monitor, solver,
physics model, and schedule. Runs until interrupted.

File contract:
    Reads:  config.json (checks mtime each cycle, reloads if changed)
    Writes: ~/.spriggler/status.json (current state, every cycle)
    Writes: ~/.spriggler/spriggler.log (structured JSON-lines event log)

The state directory defaults to ~/.spriggler/ but can be overridden
with --state-dir or SPRIGGLER_STATE_DIR.

Usage:
    spriggler-daemon --config config.json
    python -m spriggler --config config.json
"""

import argparse
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from spriggler.config.loader import load_config, ConfigError
from spriggler.sensors.registry import get_sensor_driver
from spriggler.devices.registry import get_device_driver
from spriggler.logging import StructuredLogger
from spriggler.physics.model import make_solver_predict_fn
from spriggler.safety.monitor import SafetyMonitor
from spriggler.schedule import resolve_all_targets, resolve_all_device_overrides
from spriggler.solver.solver import Solver
from spriggler.state import resolve_state_dir, ensure_state_dir
from spriggler.units import format_temp


log = logging.getLogger('spriggler')


class Daemon:
    """The main Spriggler control loop."""

    def __init__(self, config: dict, config_path: Path,
                 state_dir: Path, slog: StructuredLogger) -> None:
        self._config = config
        self._config_path = config_path
        self._config_mtime = config_path.stat().st_mtime
        self._config_error = None
        self._original_unit = config.get('_original_unit', 'F')
        self._running = False
        self._cycle = 0
        self._slog = slog

        # State directory holds all daemon output
        self._state_dir = state_dir
        self._status_path = state_dir / 'status.json'

        # Instantiate drivers
        self._sensors = {}
        self._devices = {}
        self._init_drivers()

        # Build device-environment map for physics model
        self._device_env_map = {
            dev_id: dev['environment']
            for dev_id, dev in config['devices'].items()
        }

        # Core components
        self._safety = SafetyMonitor(config)
        self._solver = Solver(config)

        # Calibration data — empty until calibration runs.
        # For now, use placeholder values so the loop can run.
        self._calibration = self._default_calibration()

        # Cycle timing
        self._cycle_seconds = config.get('safety', {}).get(
            'safety_loop_interval_seconds', 60
        )

        # Manual override tracking: {device_id: expiry_timestamp}
        self._manual_overrides = {}

        # Last commanded state per device (for override detection)
        self._last_commanded = {dev_id: 'off' for dev_id in self._devices}

        # Per-cycle state for status.json
        self._last_readings = {}
        self._last_ambient = {}
        self._last_targets = {}
        self._last_solver_result = None
        self._last_alerts = []

    def _init_drivers(self) -> None:
        """Instantiate all sensor and device drivers from config."""
        for sensor_id, sensor_cfg in self._config['sensors'].items():
            driver_cls = get_sensor_driver(sensor_cfg['driver'])
            driver = driver_cls(sensor_cfg['driver_config'])
            self._sensors[sensor_id] = {
                'driver': driver,
                'environment': sensor_cfg['environment'],
                'properties': sensor_cfg['properties'],
            }

        for device_id, device_cfg in self._config['devices'].items():
            driver_cls = get_device_driver(device_cfg['driver'])
            driver = driver_cls(device_cfg['driver_config'])
            self._devices[device_id] = {
                'driver': driver,
                'environment': device_cfg['environment'],
                'circuit': device_cfg['circuit'],
                'role': device_cfg['role'],
            }

    def _default_calibration(self) -> dict:
        """Placeholder calibration with reasonable defaults.

        Real calibration will replace this. These values let the
        loop run with mock drivers and produce sensible-looking output.
        """
        cal = {}
        for env_id in self._config['environments']:
            cal[env_id] = {
                'envelope': {'temperature': 0.1, 'humidity': 0.05},
                'devices': {},
            }
            for dev_id, dev_cfg in self._config['devices'].items():
                if dev_cfg['environment'] != env_id:
                    continue
                role = dev_cfg['role']
                driver = self._devices[dev_id]['driver']
                states = driver.get_available_states()

                dev_cal = {}
                for i, state in enumerate(states):
                    if state == 'off':
                        dev_cal[state] = {'temperature': 0.0}
                    elif role in ('heater',):
                        fraction = i / (len(states) - 1)
                        dev_cal[state] = {'temperature': 5.0 * fraction}
                    elif role in ('exhaust',):
                        fraction = i / (len(states) - 1)
                        dev_cal[state] = {'temperature': -3.0 * fraction}
                    elif role in ('humidifier',):
                        fraction = i / (len(states) - 1)
                        dev_cal[state] = {'humidity': 4.0 * fraction}
                    elif role in ('dehumidifier',):
                        fraction = i / (len(states) - 1)
                        dev_cal[state] = {'humidity': -4.0 * fraction}
                    else:
                        dev_cal[state] = {'temperature': 0.0}

                cal[env_id]['devices'][dev_id] = dev_cal

        return cal

    # ── Main loop ────────────────────────────────────────────────────────

    def run(self) -> None:
        """Run the main control loop until interrupted."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self._slog.emit('daemon.start',
                        config_name=self._config.get('name', 'unnamed'),
                        config_path=str(self._config_path),
                        state_dir=str(self._state_dir),
                        environments=', '.join(self._config['environments'].keys()),
                        sensor_count=len(self._sensors),
                        device_count=len(self._devices),
                        cycle_seconds=self._cycle_seconds,
                        display_unit=self._original_unit,
                        status_path=str(self._status_path),
                        log_path=str(self._slog._log_path))

        while self._running:
            self._cycle += 1
            try:
                self._check_config_reload()
                self._run_cycle(self._cycle)
            except Exception:
                log.exception("Error in cycle %d", self._cycle)

            # Write status after every cycle, even on error
            try:
                self._write_status()
            except Exception:
                log.exception("Failed to write status.json")

            if self._running:
                time.sleep(self._cycle_seconds)

        self._slog.emit('daemon.stop', cycles=self._cycle)
        self._slog.close()

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        self._running = False

    # ── Config reload ────────────────────────────────────────────────────

    def _check_config_reload(self) -> None:
        """Check if config file has changed and reload if so."""
        try:
            current_mtime = self._config_path.stat().st_mtime
        except OSError:
            return  # File temporarily unavailable, skip

        if current_mtime == self._config_mtime:
            return

        try:
            new_config = load_config(self._config_path)
            self._config = new_config
            self._config_mtime = current_mtime
            self._config_error = None
            self._original_unit = new_config.get('_original_unit', 'F')
            self._slog.display_unit = self._original_unit

            # Rebuild components with new config
            self._device_env_map = {
                dev_id: dev['environment']
                for dev_id, dev in new_config['devices'].items()
            }
            self._safety = SafetyMonitor(new_config)
            self._solver = Solver(new_config)
            self._cycle_seconds = new_config.get('safety', {}).get(
                'safety_loop_interval_seconds', 60
            )

            self._slog.emit('config.reload', cycle=self._cycle, success=True)
        except (ConfigError, Exception) as e:
            self._config_error = str(e)
            self._config_mtime = current_mtime  # Don't retry every cycle
            self._slog.emit('config.reload', cycle=self._cycle,
                            success=False, error=str(e))

    # ── Cycle ────────────────────────────────────────────────────────────

    def _run_cycle(self, cycle: int) -> None:
        """Execute one control cycle."""
        now = datetime.now()
        self._slog.emit('cycle.start', cycle=cycle,
                        time=now.strftime('%H:%M:%S'))

        # ── 1. Read sensors ──────────────────────────────────────────────
        readings = {}
        ambient = {}

        for sensor_id, sensor_info in self._sensors.items():
            driver = sensor_info['driver']
            env_id = sensor_info['environment']

            reading = driver.read()
            if reading is None:
                self._slog.emit('sensor.missed', cycle=cycle,
                                sensor_id=sensor_id,
                                environment=env_id)
                self._safety.report_missed_poll(sensor_id)
                continue

            self._safety.report_sensor_reading(
                sensor_id, reading, now.timestamp()
            )

            if env_id == 'ambient':
                ambient.update(reading)
                self._slog.emit('sensor.reading', cycle=cycle,
                                sensor_id=sensor_id,
                                environment='ambient',
                                **reading)
            else:
                if env_id not in readings:
                    readings[env_id] = {}
                readings[env_id].update(reading)
                self._slog.emit('sensor.reading', cycle=cycle,
                                sensor_id=sensor_id,
                                environment=env_id,
                                **reading)

        self._last_readings = readings
        self._last_ambient = ambient

        # ── 2. Safety monitor evaluate ───────────────────────────────────
        commands, alerts = self._safety.evaluate(now.timestamp())
        self._last_alerts = alerts

        for alert in alerts:
            self._slog.emit('safety.alert', cycle=cycle,
                            level=alert.level.name,
                            message=alert.message)

        safe_mode_envs = [
            env_id for env_id in self._config['environments']
            if self._safety.is_environment_in_safe_mode(env_id)
        ]

        if safe_mode_envs:
            self._slog.emit('safety.safe_mode', cycle=cycle,
                            environments=safe_mode_envs)

        if commands:
            for dev_id, state in commands:
                self._execute_device_state(dev_id, state)
                self._slog.emit('safety.command', cycle=cycle,
                                device_id=dev_id, state=state)
            if safe_mode_envs:
                self._last_solver_result = None
                return

        # ── 3. Expire manual overrides ───────────────────────────────────
        now_ts = now.timestamp()
        expired = [
            dev_id for dev_id, expiry in self._manual_overrides.items()
            if now_ts >= expiry
        ]
        for dev_id in expired:
            del self._manual_overrides[dev_id]
            self._slog.emit('override.expired', cycle=cycle,
                            device_id=dev_id)

        # ── 4. Resolve schedule ──────────────────────────────────────────
        targets = resolve_all_targets(self._config, now)
        overrides = resolve_all_device_overrides(self._config, now)
        self._last_targets = targets

        # Add manual overrides to schedule overrides
        for dev_id in self._manual_overrides:
            driver = self._devices[dev_id]['driver']
            overrides[dev_id] = driver.get_current_state()

        # ── 5. Build physics predict_fn ──────────────────────────────────
        predict_fn = make_solver_predict_fn(
            ambient=ambient,
            calibration=self._calibration,
            device_env_map=self._device_env_map,
        )

        # ── 6. Solve ─────────────────────────────────────────────────────
        device_states_available = {
            dev_id: info['driver'].get_available_states()
            for dev_id, info in self._devices.items()
        }

        locked_out = {
            dev_id for dev_id in self._devices
            if self._safety.is_device_locked_out(dev_id)
        }

        device_amps = self._estimate_device_amps()

        result = self._solver.solve(
            current_readings=readings,
            device_states_available=device_states_available,
            locked_out_devices=locked_out,
            schedule_overrides=overrides,
            predict_fn=predict_fn,
            current_phase_targets=targets,
            device_amps=device_amps,
        )
        self._last_solver_result = result

        self._slog.emit('solver.result', cycle=cycle,
                        feasible=result.feasible_count,
                        total=result.total_count,
                        cost=round(result.total_cost, 4),
                        device_states=result.device_states)

        # ── 7. Execute device commands ───────────────────────────────────
        for dev_id, state in result.device_states.items():
            driver = self._devices[dev_id]['driver']
            current = driver.get_current_state()
            if state != current:
                self._execute_device_state(dev_id, state)
                self._slog.emit('device.command', cycle=cycle,
                                device_id=dev_id,
                                old_state=current,
                                new_state=state)

        # ── 8. Detect manual overrides ───────────────────────────────────
        self._check_manual_overrides(cycle, now_ts)

        # ── 9. Log power and environment summary ─────────────────────────
        for dev_id, info in self._devices.items():
            driver = info['driver']
            power = driver.get_power()
            if power is not None and power > 0:
                self._slog.emit('device.power', cycle=cycle,
                                device_id=dev_id, watts=power)

        for env_id, env_readings in readings.items():
            env_targets = targets.get(env_id, {})
            temp = env_readings.get('temperature')
            target = env_targets.get('temperature', {})
            if temp is not None and target:
                self._slog.emit('environment.summary', cycle=cycle,
                                environment=env_id,
                                temperature=temp,
                                humidity=env_readings.get('humidity'),
                                target_min=target.get('min'),
                                target_max=target.get('max'),
                                target_ideal=target.get('ideal'))

    # ── Manual override detection ────────────────────────────────────────

    def _check_manual_overrides(self, cycle: int, now_ts: float) -> None:
        """Check if any device state differs from what was commanded."""
        for dev_id, info in self._devices.items():
            if dev_id in self._manual_overrides:
                continue

            driver = info['driver']
            actual = driver.get_current_state()
            commanded = self._last_commanded.get(dev_id, 'off')

            if actual == commanded:
                continue

            dev_cfg = self._config['devices'].get(dev_id, {})
            override_minutes = dev_cfg.get('manual_override_minutes', 0)

            if override_minutes > 0:
                expiry = now_ts + (override_minutes * 60)
                self._manual_overrides[dev_id] = expiry
                self._slog.emit('override.detected', cycle=cycle,
                                device_id=dev_id,
                                actual_state=actual,
                                commanded_state=commanded,
                                hold_minutes=override_minutes)
            else:
                self._slog.emit('override.mismatch', cycle=cycle,
                                device_id=dev_id,
                                actual_state=actual,
                                commanded_state=commanded)
                self._execute_device_state(dev_id, commanded)

    # ── Device execution ─────────────────────────────────────────────────

    def _execute_device_state(self, device_id: str, state: str) -> None:
        """Set a device to a specific state and report to safety monitor."""
        driver = self._devices[device_id]['driver']
        driver.set_state(state)
        self._last_commanded[device_id] = state
        self._safety.report_device_command(
            device_id, state != 'off', time.time()
        )

    # ── Status output ────────────────────────────────────────────────────

    def _write_status(self) -> None:
        """Write status.json with current state. All values in SI."""
        now = datetime.now(timezone.utc)

        environments = {}
        for env_id in self._config['environments']:
            environments[env_id] = {
                'readings': self._last_readings.get(env_id, {}),
                'targets': self._last_targets.get(env_id, {}),
                'safe_mode': self._safety.is_environment_in_safe_mode(env_id),
            }

        devices = {}
        for dev_id, info in self._devices.items():
            driver = info['driver']
            power = driver.get_power()
            dev_state = self._safety.get_device_state(dev_id)

            dev_status = {
                'state': driver.get_current_state(),
                'power_watts': power,
                'locked_out': self._safety.is_device_locked_out(dev_id),
                'manual_override': dev_id in self._manual_overrides,
            }
            if dev_state and dev_state.continuous_runtime:
                dev_status['runtime_seconds'] = round(
                    dev_state.continuous_runtime, 1
                )

            devices[dev_id] = dev_status

        sensors = {}
        for sensor_id in self._sensors:
            sensor_state = self._safety.get_sensor_state(sensor_id)
            if sensor_state:
                sensors[sensor_id] = {
                    'stale': sensor_state.is_stale,
                    'missed_polls': sensor_state.missed_polls,
                    'battery': sensor_state.last_battery,
                    'signal_strength': sensor_state.last_rssi,
                }

        solver = {}
        if self._last_solver_result:
            r = self._last_solver_result
            solver = {
                'last_cost': round(r.total_cost, 4),
                'feasible_combinations': r.feasible_count,
                'total_combinations': r.total_count,
            }

        status = {
            'timestamp': now.isoformat(),
            'cycle': self._cycle,
            'config_mtime': datetime.fromtimestamp(
                self._config_mtime, tz=timezone.utc
            ).isoformat(),
            'config_error': self._config_error,
            'environments': environments,
            'devices': devices,
            'sensors': sensors,
            'ambient': self._last_ambient,
            'solver': solver,
        }

        tmp_path = self._status_path.with_suffix('.tmp')
        with open(tmp_path, 'w') as f:
            json.dump(status, f, indent=2, default=str)
            f.write('\n')
        os.replace(tmp_path, self._status_path)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _estimate_device_amps(self) -> dict[str, dict[str, float]]:
        """Estimate amps per device per state."""
        amps = {}
        for dev_id, dev_cfg in self._config['devices'].items():
            role = dev_cfg['role']
            driver = self._devices[dev_id]['driver']
            states = driver.get_available_states()

            dev_amps = {}
            for i, state in enumerate(states):
                if state == 'off':
                    dev_amps[state] = 0.0
                else:
                    fraction = i / (len(states) - 1) if len(states) > 1 else 1.0
                    if role == 'heater':
                        dev_amps[state] = 12.5 * fraction
                    elif role in ('exhaust', 'intake', 'circulation', 'transfer'):
                        dev_amps[state] = 0.5 * fraction
                    elif role == 'humidifier':
                        dev_amps[state] = 0.3 * fraction
                    elif role == 'dehumidifier':
                        dev_amps[state] = 3.0 * fraction
                    elif role == 'light':
                        dev_amps[state] = 3.0 * fraction
                    else:
                        dev_amps[state] = 1.0 * fraction

            amps[dev_id] = dev_amps

        return amps


def main():
    parser = argparse.ArgumentParser(
        description='Spriggler environmental control daemon'
    )
    parser.add_argument('--config', required=True,
                        help='Path to config JSON file')
    parser.add_argument('--state-dir',
                        help='State directory for output files '
                             '(default: ~/.spriggler/)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Debug logging')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress console output (log file only)')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s  %(name)-12s  %(levelname)-7s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    config_path = Path(args.config).resolve()
    config = load_config(config_path)

    state_dir = ensure_state_dir(resolve_state_dir(args.state_dir))
    log_path = state_dir / 'spriggler.log'
    display_unit = config.get('_original_unit', 'F')

    slog = StructuredLogger(
        log_path=log_path,
        display_unit=display_unit,
        console=not args.quiet,
    )

    daemon = Daemon(config, config_path, state_dir, slog)
    daemon.run()


if __name__ == '__main__':
    main()