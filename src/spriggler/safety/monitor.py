"""Safety monitor - independent watchdog with veto authority over the solver.

The safety monitor runs on its own loop, independent of the solver.
It evaluates sensor data against absolute limits, tracks sensor liveness,
monitors device-sensor coherence, and forces devices to safe states
when conditions warrant.

The safety monitor does NOT make optimization decisions. It enforces
hard boundaries and detects failures. The solver optimizes within
those boundaries.

Design principles:
    - Independent of the solver. If the solver crashes, safety still runs.
    - Conservative. When in doubt, go to safe state.
    - No sensor data = no device operation.
    - Auto-recovery. When valid data resumes, normal operation resumes.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable


class AlertLevel(Enum):
    """Severity levels for safety alerts."""
    INFO = auto()
    WARNING = auto()
    CRITICAL = auto()
    EMERGENCY = auto()


@dataclass
class Alert:
    """A safety alert to be logged and/or sent to the user."""
    level: AlertLevel
    source: str       # e.g., "sensor:govee_veg", "device:veg_heater"
    message: str


@dataclass
class SensorState:
    """Tracked state for a single sensor."""
    sensor_id: str
    environment: str
    last_reading: dict | None = None
    missed_polls: int = 0
    is_stale: bool = False
    last_battery: float | None = None
    last_rssi: float | None = None


@dataclass
class DeviceState:
    """Tracked state for a single device."""
    device_id: str
    environment: str
    safe_state: str             # 'on', 'off', 'current'
    is_locked_out: bool = False
    in_safe_mode: bool = False
    commanded_on: bool = False
    commanded_at: float = 0.0   # timestamp
    continuous_runtime: float = 0.0  # seconds
    coherence_window: float = 300.0
    max_continuous_runtime: float | None = None


class SafetyMonitor:
    """Evaluates sensor data and enforces safety constraints.

    The safety monitor is stateful. It tracks sensor liveness, device
    coherence, and cumulative runtime. It is fed sensor readings and
    timestamps by the daemon's main loop, and returns lists of
    device commands and alerts.
    """

    def __init__(self, config: dict) -> None:
        """Initialize from a validated config dict."""
        self._config = config
        self._safety = config['safety']
        self._sensors: dict[str, SensorState] = {}
        self._devices: dict[str, DeviceState] = {}
        self._alerts: list[Alert] = []
        self._environments_in_safe_mode: set[str] = set()

        self._stale_threshold = self._safety.get('sensor_stale_after_missed', 3)
        self._battery_warning = self._safety.get('battery_warning_percent', 20)
        self._battery_critical = self._safety.get('battery_critical_percent', 5)
        self._rssi_warning = self._safety.get('rssi_warning_dbm', -90)

        self._init_sensors(config)
        self._init_devices(config)

    def _init_sensors(self, config: dict) -> None:
        for sensor_id, sensor_cfg in config['sensors'].items():
            self._sensors[sensor_id] = SensorState(
                sensor_id=sensor_id,
                environment=sensor_cfg['environment'],
            )

    def _init_devices(self, config: dict) -> None:
        device_safety = self._safety.get('devices', {})
        for device_id, device_cfg in config['devices'].items():
            dev_safety = device_safety.get(device_id, {})
            max_runtime = dev_safety.get('max_continuous_runtime_minutes')

            self._devices[device_id] = DeviceState(
                device_id=device_id,
                environment=device_cfg['environment'],
                safe_state=dev_safety.get('safe_state', 'off'),
                coherence_window=dev_safety.get('coherence_window_seconds', 300),
                max_continuous_runtime=max_runtime * 60 if max_runtime else None,
            )

    # ── Public interface ─────────────────────────────────────────────────

    def report_sensor_reading(
            self, sensor_id: str, reading: dict, timestamp: float
    ) -> None:
        """Report a successful sensor reading.

        Args:
            sensor_id: The sensor that reported.
            reading: Dict of property -> value in user units.
            timestamp: When the reading was taken (epoch seconds).
        """
        sensor = self._sensors.get(sensor_id)
        if not sensor:
            return

        sensor.last_reading = reading
        sensor.missed_polls = 0

        # Auto-recovery: if sensor was stale, clear it
        if sensor.is_stale:
            sensor.is_stale = False
            self._alert(AlertLevel.INFO, f"sensor:{sensor_id}",
                        f"Sensor '{sensor_id}' back online. Resuming normal operation.")
            self._reevaluate_environment_safe_mode(sensor.environment)

        # Track battery
        if 'battery' in reading:
            sensor.last_battery = reading['battery']

        # Track RSSI
        if 'signal_strength' in reading:
            sensor.last_rssi = reading['signal_strength']

    def report_missed_poll(self, sensor_id: str) -> None:
        """Report that a sensor poll returned no data.

        Args:
            sensor_id: The sensor that missed its poll.
        """
        sensor = self._sensors.get(sensor_id)
        if not sensor:
            return

        sensor.missed_polls += 1

        if not sensor.is_stale and sensor.missed_polls >= self._stale_threshold:
            sensor.is_stale = True
            self._alert(AlertLevel.CRITICAL, f"sensor:{sensor_id}",
                        f"Sensor '{sensor_id}' stale after {sensor.missed_polls} "
                        f"missed polls. Environment '{sensor.environment}' "
                        f"entering safe mode.")
            self._enter_safe_mode(sensor.environment)

    def report_device_command(
            self, device_id: str, commanded_on: bool, timestamp: float
    ) -> None:
        """Report that a device command was issued.

        Args:
            device_id: The device that was commanded.
            commanded_on: True if turned on, False if turned off.
            timestamp: When the command was issued (epoch seconds).
        """
        device = self._devices.get(device_id)
        if not device:
            return

        if commanded_on and not device.commanded_on:
            # Turning on: start runtime tracking
            device.commanded_at = timestamp
            device.continuous_runtime = 0.0
        elif not commanded_on:
            # Turning off: reset runtime
            device.continuous_runtime = 0.0

        device.commanded_on = commanded_on

    def evaluate(self, timestamp: float) -> tuple[list[tuple[str, str]], list[Alert]]:
        """Run a safety evaluation cycle.

        This is called on every safety loop iteration.

        Args:
            timestamp: Current time (epoch seconds).

        Returns:
            A tuple of:
            - List of (device_id, target_state) commands to execute
            - List of new alerts generated this cycle

        The caller is responsible for executing the device commands.
        """
        self._alerts = []
        commands = []

        # Check battery and RSSI across all sensors
        self._check_battery_and_rssi()

        # Check absolute limits for each environment
        limit_commands = self._check_absolute_limits()
        commands.extend(limit_commands)

        # Check rate of change
        # (requires historical data - tracked externally for now)

        # Check device coherence
        coherence_commands = self._check_device_coherence(timestamp)
        commands.extend(coherence_commands)

        # Check continuous runtime
        runtime_commands = self._check_continuous_runtime(timestamp)
        commands.extend(runtime_commands)

        # Enforce safe mode for environments with stale sensors
        safe_commands = self._enforce_safe_mode()
        commands.extend(safe_commands)

        return commands, list(self._alerts)

    def is_environment_in_safe_mode(self, environment: str) -> bool:
        """Check if an environment is currently in safe mode."""
        return environment in self._environments_in_safe_mode

    def is_device_locked_out(self, device_id: str) -> bool:
        """Check if a device has been locked out by the safety monitor."""
        device = self._devices.get(device_id)
        return device.is_locked_out if device else False

    def get_sensor_state(self, sensor_id: str) -> SensorState | None:
        """Get the tracked state for a sensor."""
        return self._sensors.get(sensor_id)

    def get_device_state(self, device_id: str) -> DeviceState | None:
        """Get the tracked state for a device."""
        return self._devices.get(device_id)

    # ── Internal checks ──────────────────────────────────────────────────

    def _check_battery_and_rssi(self) -> None:
        """Check battery and signal strength for all sensors."""
        for sensor_id, sensor in self._sensors.items():
            if sensor.last_battery is not None:
                if sensor.last_battery <= self._battery_critical:
                    self._alert(
                        AlertLevel.CRITICAL, f"sensor:{sensor_id}",
                        f"Sensor '{sensor_id}' battery critically low: "
                        f"{sensor.last_battery}%"
                    )
                elif sensor.last_battery <= self._battery_warning:
                    self._alert(
                        AlertLevel.WARNING, f"sensor:{sensor_id}",
                        f"Sensor '{sensor_id}' battery low: "
                        f"{sensor.last_battery}%"
                    )

            if sensor.last_rssi is not None:
                if sensor.last_rssi < self._rssi_warning:
                    self._alert(
                        AlertLevel.WARNING, f"sensor:{sensor_id}",
                        f"Sensor '{sensor_id}' signal weak: "
                        f"{sensor.last_rssi} dBm"
                    )

    def _check_absolute_limits(self) -> list[tuple[str, str]]:
        """Check if any environment has breached absolute limits.

        Instead of blanket safe mode, issue corrective commands:
        - Temperature below min → heaters ON, coolers/fans OFF
        - Temperature above max → heaters OFF, coolers/fans ON
        - Humidity below min → humidifiers ON, dehumidifiers OFF
        - Humidity above max → humidifiers OFF, dehumidifiers ON
        """
        commands = []
        env_safety = self._safety.get('environments', {})

        # Role → property → direction mappings
        HEATING_ROLES = {'heater', 'light'}
        COOLING_ROLES = {'cooler', 'dehumidifier', 'exhaust', 'intake',
                         'circulation', 'fan', 'vent'}
        HUMIDIFYING_ROLES = {'humidifier'}
        DEHUMIDIFYING_ROLES = {'dehumidifier', 'exhaust'}

        for sensor_id, sensor in self._sensors.items():
            if sensor.is_stale or sensor.last_reading is None:
                continue
            if sensor.environment == 'ambient':
                continue

            limits = env_safety.get(sensor.environment, {}).get('limits', {})

            for prop, limit in limits.items():
                value = sensor.last_reading.get(prop)
                if value is None:
                    continue

                abs_min = limit.get('absolute_min')
                abs_max = limit.get('absolute_max')

                if abs_min is not None and value < abs_min:
                    self._alert(
                        AlertLevel.EMERGENCY, f"env:{sensor.environment}",
                        f"ABSOLUTE MINIMUM breached in '{sensor.environment}': "
                        f"{prop} = {value} (limit: {abs_min})"
                    )
                    cmds = self._corrective_commands(
                        sensor.environment, prop, 'below_min')
                    commands.extend(cmds)

                if abs_max is not None and value > abs_max:
                    self._alert(
                        AlertLevel.EMERGENCY, f"env:{sensor.environment}",
                        f"ABSOLUTE MAXIMUM breached in '{sensor.environment}': "
                        f"{prop} = {value} (limit: {abs_max})"
                    )
                    cmds = self._corrective_commands(
                        sensor.environment, prop, 'above_max')
                    commands.extend(cmds)

        return commands

    def _corrective_commands(
            self, environment: str, prop: str, violation: str
    ) -> list[tuple[str, str]]:
        """Generate device commands to correct a limit violation.

        Args:
            environment: The affected environment.
            prop: The property that breached ('temperature', 'humidity').
            violation: 'below_min' or 'above_max'.

        Returns:
            List of (device_id, target_state) commands.
        """
        HEATING_ROLES = {'heater', 'light'}
        COOLING_ROLES = {'cooler', 'dehumidifier', 'exhaust', 'intake',
                         'circulation', 'fan', 'vent'}
        HUMIDIFYING_ROLES = {'humidifier'}
        DEHUMIDIFYING_ROLES = {'dehumidifier', 'exhaust'}

        commands = []
        for device_id, device in self._devices.items():
            if device.environment != environment:
                continue
            if device.is_locked_out:
                continue

            role = self._config['devices'][device_id].get('role', '')

            if prop == 'temperature':
                if violation == 'below_min':
                    # Too cold: heaters on, coolers off
                    if role in HEATING_ROLES:
                        commands.append((device_id, 'on'))
                    elif role in COOLING_ROLES:
                        commands.append((device_id, 'off'))
                elif violation == 'above_max':
                    # Too hot: heaters off, coolers on
                    if role in HEATING_ROLES:
                        commands.append((device_id, 'off'))
                    elif role in COOLING_ROLES:
                        commands.append((device_id, 'on'))

            elif prop == 'humidity':
                if violation == 'below_min':
                    # Too dry: humidifiers on, dehumidifiers off
                    if role in HUMIDIFYING_ROLES:
                        commands.append((device_id, 'on'))
                    elif role in DEHUMIDIFYING_ROLES:
                        commands.append((device_id, 'off'))
                elif violation == 'above_max':
                    # Too humid: humidifiers off, dehumidifiers on
                    if role in HUMIDIFYING_ROLES:
                        commands.append((device_id, 'off'))
                    elif role in DEHUMIDIFYING_ROLES:
                        commands.append((device_id, 'on'))

        return commands

    def _check_device_coherence(self, timestamp: float) -> list[tuple[str, str]]:
        """Check if device commands are producing expected sensor effects.

        If a heater has been on for longer than its coherence window
        and the temperature hasn't risen, the device may be dead.
        """
        commands = []

        for device_id, device in self._devices.items():
            if device.is_locked_out or not device.commanded_on:
                continue

            elapsed = timestamp - device.commanded_at
            if elapsed < device.coherence_window:
                continue

            # Device has been on longer than coherence window.
            # Check if the environment sensor shows the expected effect.
            env = device.environment
            role = self._config['devices'][device_id].get('role', '')

            # Find sensors for this environment
            env_sensors = [
                s for s in self._sensors.values()
                if s.environment == env and s.last_reading is not None
                   and not s.is_stale
            ]

            if not env_sensors:
                continue

            # For heaters: temperature should not be dropping
            # For humidifiers: humidity should not be dropping
            # This is a simplified coherence check. Full implementation
            # would compare against calibrated expected rates.
            # For now, we check if the property is moving in the wrong direction.
            # This requires at least two readings, which we don't track here yet.
            # Placeholder for future implementation.

        return commands

    def _check_continuous_runtime(self, timestamp: float) -> list[tuple[str, str]]:
        """Check if any device has exceeded its max continuous runtime."""
        commands = []

        for device_id, device in self._devices.items():
            if not device.commanded_on or device.max_continuous_runtime is None:
                continue

            runtime = timestamp - device.commanded_at + device.continuous_runtime
            if runtime >= device.max_continuous_runtime:
                self._alert(
                    AlertLevel.WARNING, f"device:{device_id}",
                    f"Device '{device_id}' exceeded max continuous runtime "
                    f"({device.max_continuous_runtime / 60:.0f} minutes). "
                    f"Cycling off for safety check."
                )
                commands.append((device_id, device.safe_state))
                device.commanded_on = False
                device.continuous_runtime = 0.0

        return commands

    def _enter_safe_mode(self, environment: str) -> list[tuple[str, str]]:
        """Force all devices in an environment to their safe states."""
        commands = []
        self._environments_in_safe_mode.add(environment)

        for device_id, device in self._devices.items():
            if device.environment == environment:
                device.in_safe_mode = True
                if device.safe_state != 'current':
                    commands.append((device_id, device.safe_state))

        return commands

    def _reevaluate_environment_safe_mode(self, environment: str) -> None:
        """Check if an environment can exit safe mode.

        An environment exits safe mode when ALL its sensors are live.
        """
        env_sensors = [
            s for s in self._sensors.values()
            if s.environment == environment
        ]

        all_live = all(not s.is_stale for s in env_sensors)

        if all_live and environment in self._environments_in_safe_mode:
            self._environments_in_safe_mode.discard(environment)
            for device in self._devices.values():
                if device.environment == environment:
                    device.in_safe_mode = False
            self._alert(
                AlertLevel.INFO, f"env:{environment}",
                f"All sensors live in '{environment}'. "
                f"Exiting safe mode, resuming normal operation."
            )

    def _enforce_safe_mode(self) -> list[tuple[str, str]]:
        """Ensure devices in safe-mode environments stay in safe state."""
        commands = []
        for env in self._environments_in_safe_mode:
            for device_id, device in self._devices.items():
                if device.environment == env and device.safe_state != 'current':
                    commands.append((device_id, device.safe_state))
        return commands

    def _alert(self, level: AlertLevel, source: str, message: str) -> None:
        """Record an alert."""
        self._alerts.append(Alert(level=level, source=source, message=message))