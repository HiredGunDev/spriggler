"""Controller — the control loop that manages an environment.

Each cycle:
  1. Read all sensors, classify freshness
  2. Compute current state (SI fundamentals)
  3. Resolve schedule → current targets
  4. Evaluate each device: should it change state?
  5. Command devices that need state changes
  6. Update status for display/logging

Control decisions use calibration data:
  - Energy devices (heater, humidifier): bang-bang with coast
    compensation.  Turn off coast_overshoot before target.
  - Transfer devices (fan): activate when differential to
    ambient is favorable for the intended direction.
  - Scheduled devices (lights): follow schedule, account for
    thermal byproduct in temperature calculations.

All state is in ControllerState, which is the single source of
truth for the display and status.json.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from spriggler.sensors.base import SensorReading
from spriggler.sensors.freshness import classify_freshness, Freshness
from spriggler.calibrate.engine import (
    load_calibration, EnvironmentCalibration,
    _map_intended_properties,
)

log = logging.getLogger("spriggler.controller")


# ── State ────────────────────────────────────────────────────────

@dataclass
class DeviceState:
    """Runtime state of one device."""
    name: str
    driver_name: str
    device_type: str            # "energy", "transfer", "scheduled"
    available_states: list[str] = field(default_factory=list)
    commanded_state: str = "off"
    commanded_at: float = 0.0   # time.time() of last command
    verified: str = "—"         # "rising", "falling", "stable", "pending", "—"
    power: float | None = None
    continuous_runtime: float = 0.0  # seconds in current non-off state
    reason: str = ""            # why we're in this state


@dataclass
class SensorState:
    """Runtime state of one sensor."""
    name: str
    environment: str
    freshness: Freshness = Freshness.DEAD
    age: float = 0.0
    delivery_avg: float = 0.0
    battery: float | None = None
    reading_count: int = 0
    last_sample_time: float = 0.0


@dataclass
class PropertyState:
    """Runtime state of one controlled property."""
    name: str               # "temperature", "absolute_humidity"
    display_name: str       # "temperature", "humidity"
    current: float = 0.0
    target: float = 0.0
    delta: float = 0.0
    action: str = "—"       # "heating", "cooling", "humidifying", "venting", "at target", "coasting"
    unit: str = ""          # "°F", "g/m³"


@dataclass
class Message:
    """A timestamped log message for the display."""
    timestamp: float
    level: str              # "info", "warn", "error"
    text: str


@dataclass
class ControllerState:
    """Complete runtime state — the display reads this."""
    environment: str = ""
    schedule_period: str = ""
    schedule_desc: str = ""
    uptime: float = 0.0
    cycle_time: float = 0.0
    cycle_count: int = 0

    properties: dict[str, PropertyState] = field(default_factory=dict)
    ambient: dict[str, float] = field(default_factory=dict)
    devices: dict[str, DeviceState] = field(default_factory=dict)
    sensors: dict[str, SensorState] = field(default_factory=dict)

    passive_loss: dict[str, float] = field(default_factory=dict)  # prop → rate
    passive_diff: dict[str, float] = field(default_factory=dict)  # prop → differential

    messages: list[Message] = field(default_factory=list)
    max_messages: int = 20


# ── Controller ───────────────────────────────────────────────────

class EnvironmentController:
    """Controls one environment using calibration data.

    Parameters
    ----------
    cfg : dict
        Loaded config.
    environment : str
        Environment name.
    calibration : EnvironmentCalibration
        Calibration data for this environment.
    dry_run : bool
        If True, evaluate but don't command devices.
    """

    def __init__(self, cfg: dict, environment: str,
                 calibration: EnvironmentCalibration,
                 dry_run: bool = False) -> None:
        self._cfg = cfg
        self._env_name = environment
        self._cal = calibration
        self._dry_run = dry_run
        self._state = ControllerState(environment=environment)
        self._start_time = time.time()

        # Structured logger
        from spriggler.util.slog import StructuredLogger
        home = cfg.get("_home")
        if home:
            self._slog = StructuredLogger(Path(home))
        else:
            self._slog = None

        # Hardware references — populated by setup()
        self._sensors: dict = {}
        self._devices: dict = {}
        self._sensor_configs: dict = {}
        self._device_configs: dict = {}

        # Delivery tracking per sensor
        self._delivery_times: dict[str, list[float]] = {}

        # Safety: consecutive high-power readings while commanded off.
        # Must see 2 consecutive readings before triggering, to
        # avoid false positives from KASA relay lag.
        self._safety_strikes: dict[str, int] = {}
        self.SAFETY_STRIKE_THRESHOLD = 2
        self.SAFETY_POWER_THRESHOLD = 10.0  # watts

        # Reverse map: device_name → (strip_name, plug_name) for
        # devices backed by KASA plugs (from power_monitoring config).
        # Used to read power for non-KASA devices like VeSync.
        self._backing_plugs: dict[str, tuple[str, str]] = {}
        for strip_label, strip_data in cfg.get("power_monitoring", {}).items():
            strip_name = strip_data.get("strip", strip_label)
            for plug_name, dev_name in strip_data.get("plug_map", {}).items():
                self._backing_plugs[dev_name] = (strip_name, plug_name)

    @property
    def state(self) -> ControllerState:
        return self._state

    def msg(self, level: str, text: str) -> None:
        """Add a message to the display log."""
        m = Message(timestamp=time.time(), level=level, text=text)
        self._state.messages.append(m)
        if len(self._state.messages) > self._state.max_messages:
            self._state.messages.pop(0)
        if level == "error":
            log.error(text)
        elif level == "warn":
            log.warning(text)
        else:
            log.info(text)

    # ── Setup ────────────────────────────────────────────────────

    def setup(self) -> bool:
        """Initialize sensors and devices."""
        from spriggler.util.discovery import discover_plugins
        from spriggler.sensors import driver_registry as sensor_reg
        from spriggler.devices import driver_registry as device_reg

        discover_plugins(package="spriggler.physics")
        discover_plugins(package="spriggler.sensors",
                         exclude={"base", "freshness"})
        discover_plugins(package="spriggler.devices",
                         exclude={"kasa_mgr", "vesync_mgr"})

        # Sensors
        for name, scfg in self._cfg.get("sensors", {}).items():
            env = scfg.get("environment")
            if env not in (self._env_name, "ambient"):
                continue
            drv_cls = sensor_reg.get(scfg.get("driver"))
            if drv_cls is None:
                self.msg("warn", f"Sensor driver '{scfg.get('driver')}' not found")
                continue
            try:
                self._sensors[name] = drv_cls(
                    sensor_name=name,
                    driver_config=scfg.get("driver_config", {}),
                )
                self._sensor_configs[name] = scfg
                self._state.sensors[name] = SensorState(
                    name=name, environment=env,
                )
                self.msg("info", f"Sensor ready: {name}")
            except Exception as e:
                self.msg("error", f"Sensor {name} failed: {e}")

        # Devices
        for name, dcfg in self._cfg.get("devices", {}).items():
            if dcfg.get("environment") != self._env_name:
                continue
            drv_cls = device_reg.get(dcfg.get("driver"))
            if drv_cls is None:
                self.msg("warn", f"Device driver '{dcfg.get('driver')}' not found")
                continue
            try:
                dev = drv_cls(
                    device_name=name,
                    driver_config=dcfg.get("driver_config", {}),
                )
                self._devices[name] = dev
                self._device_configs[name] = dcfg

                dev_type = dcfg.get("type", "energy")
                if dcfg.get("scheduled"):
                    dev_type = "scheduled"

                self._state.devices[name] = DeviceState(
                    name=name,
                    driver_name=dcfg.get("driver", "?"),
                    device_type=dev_type,
                    available_states=dev.get_states(),
                )
                self.msg("info", f"Device ready: {name}")
            except Exception as e:
                self.msg("error", f"Device {name} failed: {e}")

        # Verify KASA power paths — ensure plugs backing non-KASA
        # devices (e.g., VeSync humidifier on a KASA strip) are
        # energized.  A plug turned off at the strip makes the
        # cloud API device unreachable.
        self._ensure_power_paths()

        # Establish known device state — command everything OFF.
        # A device left on from a previous run, crash, or manual
        # command is invisible to the controller.  A stuck-on heater
        # in a sealed pod is a fire hazard.
        self.msg("info", "Syncing devices to known state (all OFF)...")
        for name, device in self._devices.items():
            try:
                device.set_state("off")
                ds = self._state.devices.get(name)
                if ds:
                    ds.commanded_state = "off"
                    ds.commanded_at = 0
                    ds.verified = "—"
            except Exception as e:
                self.msg("error", f"Cannot reset {name}: {e}")

        # Wait for sensors
        self.msg("info", "Waiting for sensor data...")
        time.sleep(5)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self._read_env():
                self.msg("info", "Sensors online")
                if self._slog:
                    self._slog.log_start(
                        self._env_name,
                        len(self._devices),
                        len(self._sensors),
                        self._dry_run,
                    )
                return True
            time.sleep(1)

        self.msg("error", "No sensor data received")
        return False

    # ── Power path verification ──────────────────────────────────

    def _ensure_power_paths(self) -> None:
        """Ensure KASA plugs backing non-KASA devices are energized.

        Reads the power_monitoring config to find which KASA strip
        plugs back each device.  For devices whose driver is NOT
        kasa_plug (e.g., VeSync humidifier), ensures the backing
        KASA plug is turned on so the device has power.

        For KASA-native devices, the plug IS the device — the
        set_state command handles power directly.
        """
        power_mon = self._cfg.get("power_monitoring", {})
        if not power_mon:
            return

        # Build reverse map: device_name → (strip_name, plug_name)
        device_to_plug = {}
        for strip_label, strip_data in power_mon.items():
            strip_name = strip_data.get("strip", strip_label)
            for plug_name, dev_name in strip_data.get("plug_map", {}).items():
                device_to_plug[dev_name] = (strip_name, plug_name)

        # For each device in our environment, check if it needs
        # a KASA plug energized
        for dev_name, dcfg in self._device_configs.items():
            driver = dcfg.get("driver", "")

            # KASA devices manage their own power — skip
            if driver == "kasa_plug":
                continue

            # Non-KASA device — check if it has a backing plug
            plug_info = device_to_plug.get(dev_name)
            if plug_info is None:
                continue

            strip_name, plug_name = plug_info
            self.msg("info",
                     f"Ensuring power path: {strip_name}/{plug_name} "
                     f"→ {dev_name}")

            try:
                from spriggler.devices.kasa_mgr import (
                    get_kasa_manager, KasaError,
                )
                mgr = get_kasa_manager()
                plug = mgr.get_plug(strip_name, plug_name)
                mgr.update_device(plug)
                if not mgr.is_on(plug):
                    self.msg("warn",
                             f"KASA plug {strip_name}/{plug_name} was OFF "
                             f"— powering on for {dev_name}")
                    mgr.turn_on(plug)
                    time.sleep(3)  # Let device boot
                    if self._slog:
                        self._slog.log("startup.power_path",
                                       device=dev_name,
                                       strip=strip_name,
                                       plug=plug_name,
                                       action="powered on")
                else:
                    self.msg("info",
                             f"Power path OK: {strip_name}/{plug_name}")
            except Exception as e:
                self.msg("error",
                         f"Cannot verify power path for {dev_name}: {e}")

    # ── Sensor reading ───────────────────────────────────────────

    def _read_fresh(self, sensor_name: str) -> SensorReading | None:
        sensor = self._sensors.get(sensor_name)
        if sensor is None:
            return None
        reading = sensor.read()
        if reading is None:
            return None
        scfg = self._sensor_configs[sensor_name]
        interval = scfg.get("delivery_interval_seconds", 10)
        defaults = self._cfg.get("sensor_defaults", {})
        # Use generous freshness thresholds for the control loop.
        # The cycle takes ~5 seconds (KASA power reads), during which
        # sensor readings age.  A reading that was fresh at cycle start
        # shouldn't go stale by cycle end.
        fresh_mult = scfg.get("fresh_multiplier",
                              defaults.get("fresh_multiplier", 2.5))
        aging_mult = scfg.get("aging_multiplier",
                              defaults.get("aging_multiplier", 5.0))
        dead_mult = scfg.get("dead_multiplier",
                             defaults.get("dead_multiplier", 10.0))

        freshness = classify_freshness(
            reading, interval, fresh_mult, aging_mult, dead_mult,
        )

        # Update sensor state
        ss = self._state.sensors.get(sensor_name)
        if ss:
            ss.freshness = freshness
            ss.age = reading.age
            ss.battery = reading.get("battery")
            # Track delivery
            if reading.sample_time != ss.last_sample_time:
                if ss.last_sample_time > 0:
                    dt_list = self._delivery_times.setdefault(sensor_name, [])
                    dt_list.append(reading.sample_time - ss.last_sample_time)
                    if len(dt_list) > 20:
                        dt_list.pop(0)
                    ss.delivery_avg = sum(dt_list) / len(dt_list)
                ss.reading_count += 1
                ss.last_sample_time = reading.sample_time

        if freshness.ok_for_control:
            return reading
        return None

    def _read_env(self) -> dict[str, float] | None:
        merged = {}
        for name, scfg in self._sensor_configs.items():
            if scfg.get("environment") != self._env_name:
                continue
            reading = self._read_fresh(name)
            if reading is not None:
                merged.update(reading.values)
        return merged if merged else None

    def _read_ambient(self) -> dict[str, float] | None:
        merged = {}
        for name, scfg in self._sensor_configs.items():
            if scfg.get("environment") != "ambient":
                continue
            reading = self._read_fresh(name)
            if reading is not None:
                merged.update(reading.values)
        return merged if merged else None

    # ── Schedule resolution ──────────────────────────────────────

    def _resolve_schedule(self) -> dict:
        """Determine current schedule period and targets.

        Returns dict with keys: period_name, targets, devices.
        """
        from spriggler.physics.temperature import fahrenheit_to_kelvin
        from spriggler.physics import registry as physics_reg

        schedules = self._cfg.get("schedules", {})
        env_schedule = schedules.get(self._env_name, {})
        phases = env_schedule.get("phases", [])

        now = datetime.now()
        current_time = now.strftime("%H:%M")

        for phase in phases:
            start = phase.get("start", "00:00")
            end = phase.get("end", "23:59")

            # Handle overnight spans (e.g., 22:00 - 16:00)
            if start <= end:
                in_window = start <= current_time < end
            else:
                in_window = current_time >= start or current_time < end

            if in_window:
                targets = {}
                raw_targets = phase.get("targets", {})

                if "temperature" in raw_targets:
                    targets["temperature"] = fahrenheit_to_kelvin(
                        float(raw_targets["temperature"])
                    )
                if "humidity" in raw_targets:
                    # %RH target — we'll convert to AH using current temp
                    targets["_rh_target"] = float(raw_targets["humidity"])

                period_desc = (
                    f"{phase.get('name', '?')} "
                    f"({start}–{end})"
                )

                self._state.schedule_period = phase.get("name", "?")
                self._state.schedule_desc = period_desc

                return {
                    "period_name": phase.get("name"),
                    "targets": targets,
                    "devices": phase.get("devices", {}),
                }

        return {"period_name": "none", "targets": {}, "devices": {}}

    def _compute_ah_target(self, rh_pct: float,
                           current_temp: float) -> float:
        """Convert %RH target to absolute humidity at current temp."""
        from spriggler.calibrate.engine import _ah_from_temp_and_rh
        return _ah_from_temp_and_rh(current_temp, rh_pct / 100.0)

    # ── Control decisions ────────────────────────────────────────

    def _decide_energy_device(self, device_name: str,
                              current: dict[str, float],
                              targets: dict[str, float]) -> str | None:
        """Decide what state an energy device should be in.

        Returns the desired state, or None if no change needed.
        Uses calibration coast_overshoot for anticipatory shutoff.
        """
        dcfg = self._device_configs[device_name]
        intended = _map_intended_properties(
            dcfg.get("intended_properties", {})
        )
        device = self._devices[device_name]
        dev_state = self._state.devices[device_name]
        cal = self._cal.devices.get(device_name)

        for prop, direction in intended.items():
            current_val = current.get(prop)
            target_val = targets.get(prop)
            if current_val is None or target_val is None:
                continue

            delta = current_val - target_val

            # Get coast overshoot from calibration
            coast = 0.0
            if cal:
                for state_name, scal in cal.states.items():
                    pcal = scal.properties.get(prop)
                    if pcal:
                        coast = abs(pcal.coast_overshoot)
                        break

            # Use half the coast overshoot so the oscillation is
            # symmetric around target:
            #   ON when below target - coast/2
            #   OFF when at target - coast/2 (coasts to target + coast/2)
            # Average = target.  Band = ± coast/2.
            half_coast = coast / 2.0

            if direction == "increase":
                if delta < -half_coast:
                    # Below target - half_coast: need to activate
                    states = device.get_states()
                    desired = states[-1]  # highest
                    if dev_state.commanded_state != desired:
                        dev_state.reason = (
                            f"{prop} {abs(delta):.1f} below target"
                        )
                        return desired
                elif delta >= -half_coast and dev_state.commanded_state != "off":
                    # At or above shutoff point: turn off, coast will
                    # carry us to target + half_coast
                    dev_state.reason = f"{prop} at target (coast zone)"
                    return "off"
                else:
                    if dev_state.commanded_state == "off":
                        dev_state.reason = f"coasting ({abs(delta):.1f} to target)"

            elif direction == "decrease":
                if delta > half_coast:
                    states = device.get_states()
                    desired = states[-1]
                    if dev_state.commanded_state != desired:
                        dev_state.reason = (
                            f"{prop} {delta:.1f} above target"
                        )
                        return desired
                elif delta <= half_coast and dev_state.commanded_state != "off":
                    dev_state.reason = f"{prop} at target (coast zone)"
                    return "off"

        return None

    def _decide_scheduled_device(self, device_name: str,
                                 schedule_devices: dict) -> str | None:
        """Decide scheduled device state based on schedule."""
        desired = schedule_devices.get(device_name)
        dev_state = self._state.devices[device_name]

        if desired is None:
            # Not mentioned in current schedule period — turn off
            if dev_state.commanded_state != "off":
                dev_state.reason = "not in schedule"
                return "off"
        elif dev_state.commanded_state != desired:
            dev_state.reason = "schedule"
            return desired

        return None

    def _decide_transfer_device(self, device_name: str,
                                current: dict, ambient: dict,
                                targets: dict) -> str | None:
        """Decide transfer device state based on differential.

        The fan should run when ambient is closer to target than
        pod is — i.e., exchanging air with ambient helps.

        CONFLICT RULE: don't vent while the humidifier is actively
        adding moisture.  Venting dumps humidity faster than the
        humidifier can add it.
        """
        dev_state = self._state.devices[device_name]

        # Check if any humidity device is actively running
        humidifier_active = False
        for name, ds in self._state.devices.items():
            dcfg = self._device_configs.get(name, {})
            intended = dcfg.get("intended_properties", {})
            if "humidity" in intended and ds.commanded_state != "off":
                humidifier_active = True
                break

        if humidifier_active:
            if dev_state.commanded_state != "off":
                dev_state.reason = "holding — humidifier active"
                return "off"
            return None

        pod_temp = current.get("temperature")
        amb_temp = ambient.get("temperature")
        target_temp = targets.get("temperature")

        if pod_temp and amb_temp and target_temp:
            if pod_temp > target_temp and amb_temp < pod_temp:
                if dev_state.commanded_state != "on":
                    dev_state.reason = "venting — pod above target"
                    return "on"
            elif pod_temp <= target_temp:
                if dev_state.commanded_state != "off":
                    dev_state.reason = "pod at/below target"
                    return "off"

        return None

    # ── Command execution ────────────────────────────────────────

    def _command_device(self, device_name: str, desired_state: str) -> bool:
        """Send a command to a device. Returns True on success."""
        device = self._devices.get(device_name)
        dev_state = self._state.devices.get(device_name)
        if device is None or dev_state is None:
            return False

        if self._dry_run:
            self.msg("info",
                     f"[dry-run] {device_name} → {desired_state} "
                     f"({dev_state.reason})")
            dev_state.commanded_state = desired_state
            dev_state.commanded_at = time.time()
            dev_state.verified = "dry-run"
            if self._slog:
                self._slog.log_command(
                    device_name, dev_state.commanded_state,
                    desired_state, f"[dry-run] {dev_state.reason}")
            return True

        success = device.set_state(desired_state)
        if success:
            old_state = dev_state.commanded_state
            dev_state.commanded_state = desired_state
            dev_state.commanded_at = time.time()
            dev_state.verified = "pending"
            if desired_state == "off":
                dev_state.continuous_runtime = 0
            self.msg("info",
                     f"{device_name} → {desired_state} "
                     f"({dev_state.reason})")
            if self._slog:
                self._slog.log_command(
                    device_name, old_state,
                    desired_state, dev_state.reason)
        else:
            self.msg("error",
                     f"{device_name} → {desired_state} FAILED")
            dev_state.verified = "failed"
            if self._slog:
                self._slog.log("device.failed",
                               device=device_name,
                               desired_state=desired_state,
                               reason=dev_state.reason)

        return success

    # ── Verification ─────────────────────────────────────────────

    def _verify_devices(self, current: dict, previous: dict | None) -> None:
        """Update verification status based on sensor feedback."""
        if previous is None:
            return

        for name, dev_state in self._state.devices.items():
            if dev_state.commanded_state == "off":
                dev_state.verified = "—"
                continue

            dcfg = self._device_configs.get(name, {})
            intended = _map_intended_properties(
                dcfg.get("intended_properties", {})
            )

            for prop, direction in intended.items():
                curr = current.get(prop)
                prev = previous.get(prop)
                if curr is None or prev is None:
                    continue

                delta = curr - prev
                if direction == "increase" and delta > 0:
                    dev_state.verified = "rising"
                elif direction == "decrease" and delta < 0:
                    dev_state.verified = "falling"
                elif abs(delta) < 0.01:
                    dev_state.verified = "stable"
                else:
                    dev_state.verified = "pending"

    # ── Passive loss computation ─────────────────────────────────

    def _update_passive_loss(self, current: dict, ambient: dict) -> None:
        """Compute current passive loss rates from calibration data."""
        for prop in ("temperature", "absolute_humidity"):
            g = self._cal.passive_conductance.get(prop, 0)
            env_val = current.get(prop)
            amb_val = ambient.get(prop)
            if env_val and amb_val and amb_val > 0:
                diff = amb_val - env_val
                rate = g * diff
                self._state.passive_loss[prop] = rate
                self._state.passive_diff[prop] = diff

    # ── Main cycle ───────────────────────────────────────────────

    def cycle(self, previous_env: dict | None = None) -> dict | None:
        """Run one control cycle. Returns current env readings."""
        cycle_start = time.time()

        # 1. Read sensors
        env = self._read_env()
        amb = self._read_ambient()

        if env is None:
            # Check if any sensors are dead
            for ss in self._state.sensors.values():
                if ss.freshness == Freshness.DEAD:
                    self.msg("error",
                             f"Sensor {ss.name} is DEAD — no data")
            return previous_env

        # Update ambient display
        if amb:
            self._state.ambient = dict(amb)

        # 2. Resolve schedule
        schedule = self._resolve_schedule()
        targets = dict(schedule["targets"])

        # Convert %RH target to AH at current temperature
        rh_target = targets.pop("_rh_target", None)
        current_temp = env.get("temperature")
        if rh_target and current_temp:
            ah_target = self._compute_ah_target(rh_target, current_temp)
            targets["absolute_humidity"] = ah_target

        # 3. Update property states
        from spriggler.physics.temperature import kelvin_to_fahrenheit
        from spriggler.calibrate.engine import _rh_from_temp_and_ah

        for prop in ("temperature", "absolute_humidity"):
            current_val = env.get(prop, 0)
            target_val = targets.get(prop)

            if prop == "temperature":
                ps = self._state.properties.setdefault(prop, PropertyState(
                    name=prop, display_name="temperature", unit="°F",
                ))
                ps.current = current_val
                if target_val:
                    ps.target = target_val
                    ps.delta = current_val - target_val
            elif prop == "absolute_humidity":
                ps = self._state.properties.setdefault(prop, PropertyState(
                    name=prop, display_name="humidity", unit="g/m³",
                ))
                ps.current = current_val
                if target_val:
                    ps.target = target_val
                    ps.delta = current_val - target_val

        # 4. Make control decisions
        for name, dev_state in self._state.devices.items():
            desired = None

            if dev_state.device_type == "energy":
                desired = self._decide_energy_device(name, env, targets)
            elif dev_state.device_type == "scheduled":
                desired = self._decide_scheduled_device(
                    name, schedule.get("devices", {}))
            elif dev_state.device_type == "transfer":
                desired = self._decide_transfer_device(
                    name, env, amb or {}, targets)

            if desired is not None:
                self._command_device(name, desired)

            # Update continuous runtime
            if dev_state.commanded_state != "off":
                if dev_state.commanded_at > 0:
                    dev_state.continuous_runtime = (
                        time.time() - dev_state.commanded_at
                    )

            # Update action display
            dcfg = self._device_configs.get(name, {})
            intended = _map_intended_properties(
                dcfg.get("intended_properties", {})
            )
            for prop in intended:
                ps = self._state.properties.get(prop)
                if ps:
                    if dev_state.commanded_state != "off":
                        actions = {
                            "temperature": {"increase": "heating", "decrease": "cooling"},
                            "absolute_humidity": {"increase": "humidifying", "decrease": "dehumidifying"},
                        }
                        ps.action = actions.get(prop, {}).get(
                            intended.get(prop), dev_state.commanded_state
                        )
                    elif ps.action not in ("at target", "coasting"):
                        if ps.delta and abs(ps.delta) < 0.5:
                            ps.action = "at target"
                        else:
                            ps.action = "—"

        # 5. Verify devices from sensor feedback
        self._verify_devices(env, previous_env)

        # 6. Update passive loss
        if amb:
            self._update_passive_loss(env, amb)

        # 7. Update power readings — ALL devices, including off ones.
        # Power draw is how we verify device state.  If we commanded
        # a heater off and it's still drawing 500W, that's a safety
        # issue we must detect.
        for name, device in self._devices.items():
            if hasattr(device, "read_power"):
                try:
                    pw = device.read_power()
                    if pw is not None:
                        ds = self._state.devices.get(name)
                        if ds:
                            ds.power = pw
                            # Safety check: device should be off but
                            # drawing significant power.  Wait 10s
                            # after commanding off to let the relay
                            # open and the KASA cache update.
                            time_since_cmd = (
                                time.time() - ds.commanded_at
                                if ds.commanded_at else 999
                            )
                            if ds.commanded_state == "off" \
                               and pw > self.SAFETY_POWER_THRESHOLD \
                               and time_since_cmd > 10.0:
                                self.msg("error",
                                         f"SAFETY: {name} commanded OFF "
                                         f"{time_since_cmd:.0f}s ago "
                                         f"but drawing {pw:.0f}W — "
                                         f"re-commanding OFF")
                                device.set_state("off")
                                ds.commanded_at = time.time()
                                if self._slog:
                                    self._slog.log(
                                        "safety.stuck_device",
                                        device=name,
                                        power=pw,
                                        seconds_since_off=round(
                                            time_since_cmd, 1),
                                        action="re-command off")
                except Exception:
                    pass
            else:
                # Devices without native power monitoring (e.g. VeSync):
                # Read power from backing KASA plug if available.
                ds = self._state.devices.get(name)
                backing = self._backing_plugs.get(name)
                if ds and backing:
                    try:
                        from spriggler.devices.kasa_mgr import (
                            get_kasa_manager, KasaError,
                        )
                        mgr = get_kasa_manager()
                        pw = mgr.read_power_cached(backing[0], backing[1])
                        if pw is not None:
                            ds.power = pw
                    except Exception:
                        pass

                # Blindly re-command to desired state every ~20 cycles
                # to guard against missed commands
                if ds and self._state.cycle_count % 20 == 0:
                    try:
                        device.set_state(ds.commanded_state)
                    except Exception:
                        pass

        # Update timing
        self._state.uptime = time.time() - self._start_time
        self._state.cycle_time = time.time() - cycle_start
        self._state.cycle_count += 1

        # 8. Structured log
        if self._slog:
            self._slog.log_cycle(self._state)

            # Log sensor issues
            for name, ss in self._state.sensors.items():
                if ss.freshness in (Freshness.STALE, Freshness.DEAD):
                    self._slog.log_sensor_issue(
                        name, ss.freshness.value, ss.age)

        return env
