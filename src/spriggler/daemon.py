"""Spriggler daemon - the main control loop.

Connects all components: config, drivers, safety monitor, solver,
physics model, and schedule. Runs until interrupted.

Usage:
    python -m spriggler --config config.json
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime

from spriggler.config.loader import load_config
from spriggler.drivers.registry import get_sensor_driver, get_device_driver
from spriggler.physics.model import make_solver_predict_fn
from spriggler.safety.monitor import SafetyMonitor
from spriggler.schedule import resolve_all_targets, resolve_all_device_overrides
from spriggler.solver.solver import Solver
from spriggler.units import format_temp, from_kelvin


log = logging.getLogger('spriggler')


class Daemon:
    """The main Spriggler control loop."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._original_unit = config.get('_original_unit', 'F')
        self._running = False

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
            # Build device contributions from roles
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
                        # Spread heating across graduated states
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

    def run(self) -> None:
        """Run the main control loop until interrupted."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        log.info("Spriggler daemon starting")
        log.info("Config: %s", self._config.get('name', 'unnamed'))
        log.info("Environments: %s", ', '.join(self._config['environments'].keys()))
        log.info("Sensors: %d, Devices: %d",
                 len(self._sensors), len(self._devices))
        log.info("Cycle interval: %ds", self._cycle_seconds)
        log.info("Display unit: %s", self._original_unit)
        log.info("─" * 60)

        cycle = 0
        while self._running:
            cycle += 1
            try:
                self._run_cycle(cycle)
            except Exception:
                log.exception("Error in cycle %d", cycle)

            if self._running:
                time.sleep(self._cycle_seconds)

        log.info("Spriggler daemon stopped")

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        self._running = False

    def _run_cycle(self, cycle: int) -> None:
        """Execute one control cycle."""
        now = datetime.now()
        log.info("── Cycle %d ── %s ──", cycle, now.strftime('%H:%M:%S'))

        # ── 1. Read sensors ──────────────────────────────────────────────
        readings = {}       # {env_id: {property: value}}
        ambient = {}        # {property: value}

        for sensor_id, sensor_info in self._sensors.items():
            driver = sensor_info['driver']
            env_id = sensor_info['environment']

            reading = driver.read()
            if reading is None:
                log.warning("  sensor %-20s  NO DATA", sensor_id)
                self._safety.report_missed_poll(sensor_id)
                continue

            # Feed safety monitor
            self._safety.report_sensor_reading(
                sensor_id, reading, now.timestamp()
            )

            # Route to environment
            if env_id == 'ambient':
                ambient.update(reading)
                temp_str = self._fmt_temp(reading.get('temperature'))
                log.info("  sensor %-20s  ambient  %s", sensor_id, temp_str)
            else:
                if env_id not in readings:
                    readings[env_id] = {}
                readings[env_id].update(reading)
                temp_str = self._fmt_temp(reading.get('temperature'))
                hum_str = f"H:{reading.get('humidity', 0):.1f}%"
                log.info("  sensor %-20s  %-10s %s  %s",
                         sensor_id, env_id, temp_str, hum_str)

        # ── 2. Safety monitor evaluate ───────────────────────────────────
        commands, alerts = self._safety.evaluate(now.timestamp())

        for alert in alerts:
            log_level = {
                'INFO': logging.INFO,
                'WARNING': logging.WARNING,
                'CRITICAL': logging.CRITICAL,
                'EMERGENCY': logging.CRITICAL,
            }.get(alert.level.name, logging.WARNING)
            log.log(log_level, "  SAFETY: [%s] %s", alert.level.name, alert.message)

        # Check if any environments are in safe mode
        safe_mode_envs = {
            env_id for env_id in self._config['environments']
            if self._safety.is_environment_in_safe_mode(env_id)
        }

        if safe_mode_envs:
            log.warning("  SAFE MODE: %s", ', '.join(safe_mode_envs))
            # Execute safe state commands
            for dev_id, state in commands:
                self._execute_device_state(dev_id, state)
                log.warning("  SAFE CMD: %s → %s", dev_id, state)

        if commands:
            # If safety issued commands, execute them and skip solver
            for dev_id, state in commands:
                self._execute_device_state(dev_id, state)
            if safe_mode_envs:
                return  # Skip solver when in safe mode

        # ── 3. Resolve schedule ──────────────────────────────────────────
        targets = resolve_all_targets(self._config, now)
        overrides = resolve_all_device_overrides(self._config, now)

        # ── 4. Build physics predict_fn ──────────────────────────────────
        predict_fn = make_solver_predict_fn(
            ambient=ambient,
            calibration=self._calibration,
            device_env_map=self._device_env_map,
        )

        # ── 5. Solve ─────────────────────────────────────────────────────
        device_states_available = {
            dev_id: info['driver'].get_available_states()
            for dev_id, info in self._devices.items()
        }

        locked_out = {
            dev_id for dev_id in self._devices
            if self._safety.is_device_locked_out(dev_id)
        }

        # Device amps from config or driver (placeholder for now)
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

        log.info("  SOLVER: evaluated %d/%d combinations, cost=%.4f",
                 result.feasible_count, result.total_count, result.total_cost)

        # ── 6. Execute device commands ───────────────────────────────────
        for dev_id, state in result.device_states.items():
            driver = self._devices[dev_id]['driver']
            current = driver.get_current_state()
            if state != current:
                self._execute_device_state(dev_id, state)
                log.info("  CMD: %-20s %s → %s", dev_id, current, state)
            else:
                log.debug("  CMD: %-20s %s (no change)", dev_id, state)

        # ── 7. Log environment summary ───────────────────────────────────
        for env_id, env_readings in readings.items():
            env_targets = targets.get(env_id, {})
            temp = env_readings.get('temperature')
            target = env_targets.get('temperature', {})
            if temp is not None and target:
                t_str = self._fmt_temp(temp)
                tgt_min = self._fmt_temp(target.get('min'))
                tgt_max = self._fmt_temp(target.get('max'))
                log.info("  ENV %-12s  %s  [%s – %s]",
                         env_id, t_str, tgt_min, tgt_max)

    def _execute_device_state(self, device_id: str, state: str) -> None:
        """Set a device to a specific state and report to safety monitor."""
        driver = self._devices[device_id]['driver']
        driver.set_state(state)
        self._safety.report_device_command(
            device_id, state != 'off', time.time()
        )

    def _estimate_device_amps(self) -> dict[str, dict[str, float]]:
        """Estimate amps per device per state.

        TODO: Real values from calibration or config.
        For now, rough estimates from device roles.
        """
        amps = {}
        for dev_id, dev_cfg in self._config['devices'].items():
            role = dev_cfg['role']
            driver = self._devices[dev_id]['driver']
            states = driver.get_available_states()
            voltage = self._config['circuits'][dev_cfg['circuit']]['voltage']

            dev_amps = {}
            for i, state in enumerate(states):
                if state == 'off':
                    dev_amps[state] = 0.0
                else:
                    fraction = i / (len(states) - 1) if len(states) > 1 else 1.0
                    # Rough estimates by role
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

    def _fmt_temp(self, kelvin: float | None) -> str:
        """Format a temperature for log output."""
        if kelvin is None:
            return "-- --"
        return format_temp(kelvin, self._original_unit)


def main():
    parser = argparse.ArgumentParser(description='Spriggler environmental controller')
    parser.add_argument('--config', required=True, help='Path to config JSON file')
    parser.add_argument('--verbose', '-v', action='store_true', help='Debug logging')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s  %(name)-12s  %(levelname)-7s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    config = load_config(args.config)
    daemon = Daemon(config)
    daemon.run()


if __name__ == '__main__':
    main()
