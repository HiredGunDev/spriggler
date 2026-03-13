"""Calibration engine — record everything, analyze after.

RECORD:
    Turn device on.  Record every sensor reading (pod + ambient)
    with timestamps.  Wait for a solid differential.
    Turn device off.  Keep recording until we return near baseline.

    For non-temperature devices, hold temperature at operating
    conditions using the heater (requires heater to be calibrated
    first).  The heater's calibrated coast_overshoot defines the
    control band.

ANALYZE:
    Take the complete time series and extract:
      - Activation rate (slope during device-on phase)
      - Coast profile (trajectory after shutoff)
      - Coast overshoot (total change after shutoff)
      - Thermal byproduct (temperature change when temp isn't intended)

    For humidity calibration, compensate observed dAH/dt for
    temperature-induced AH changes using the Magnus formula.
    This isolates the device's true moisture contribution from
    thermal contamination.

PRECONDITION DEPENDENCIES:
    Some devices require other devices to be calibrated first.
    - Heater: no preconditions (calibrate cold, first in line)
    - Humidifier: requires heater calibration (thermal envelope)
    - Fan/transfer: requires heater calibration (thermal differential)
    The engine checks for required calibrations before proceeding.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spriggler.sensors.base import SensorReading
from spriggler.sensors.freshness import classify_freshness, Freshness

log = logging.getLogger("spriggler.calibrate")


# ── Data structures ──────────────────────────────────────────────

@dataclass
class Sample:
    """One timestamped observation during calibration."""
    elapsed: float              # Seconds since recording started
    timestamp: float            # Wall clock (time.time())
    env: dict[str, float]       # Environment sensor values (SI)
    ambient: dict[str, float]   # Ambient sensor values (SI)
    power: float | None = None  # Watts (if available)


@dataclass
class Recording:
    """Complete recording of a calibration run for one device state."""
    device_name: str
    state: str
    samples: list[Sample] = field(default_factory=list)
    activation_index: int = 0
    deactivation_index: int = 0
    ambient_temp_at_start: float | None = None
    thermal_hold_active: bool = False  # Was heater cycling during this run?


@dataclass
class PropertyCalibration:
    """Analyzed calibration for one property of one device state."""
    property_name: str
    rate: float                 # units/second during activation
    coast_overshoot: float
    coast_duration: float
    coast_profile: list[tuple[float, float]]
    rate_raw: float | None = None   # before thermal compensation (if applicable)


@dataclass
class StateCalibration:
    """Analyzed calibration for one device state."""
    state: str
    properties: dict[str, PropertyCalibration] = field(default_factory=dict)
    thermal_byproduct_rate: float | None = None
    power_draw: float | None = None
    avg_temp_during_activation: float | None = None


@dataclass
class DeviceCalibration:
    """Analyzed calibration for one device."""
    device_name: str
    states: dict[str, StateCalibration] = field(default_factory=dict)
    calibrated_at: float = 0.0
    ambient_temp_during_cal: float | None = None


@dataclass
class EnvironmentCalibration:
    """Analyzed calibration for one environment."""
    environment_name: str
    devices: dict[str, DeviceCalibration] = field(default_factory=dict)
    passive_conductance: dict[str, float] = field(default_factory=dict)
    time_constant: dict[str, float] = field(default_factory=dict)
    calibrated_at: float = 0.0


# ── Property name mapping ────────────────────────────────────────

_PROPERTY_MAP = {
    "humidity": "absolute_humidity",
    "temperature": "temperature",
}


def _map_intended_properties(intended: dict) -> dict:
    """Map config property names to SI internal names."""
    return {
        _PROPERTY_MAP.get(prop, prop): direction
        for prop, direction in intended.items()
    }


# ── AH temperature compensation ─────────────────────────────────

def _saturation_vapor_pressure(temp_k: float) -> float:
    """Saturation vapor pressure in Pa via Magnus formula.

    Alduchov & Eskridge 1996 constants.
    """
    temp_c = temp_k - 273.15
    a = 17.625
    b = 243.04
    c = 610.94
    return c * math.exp(a * temp_c / (b + temp_c))


def _ah_from_temp_and_rh(temp_k: float, rh_frac: float) -> float:
    """Compute absolute humidity (g/m³) from temperature and RH fraction."""
    e = rh_frac * _saturation_vapor_pressure(temp_k)
    Rv = 461.5  # specific gas constant for water vapor
    return (e / (Rv * temp_k)) * 1000.0  # kg/m³ → g/m³


def _rh_from_temp_and_ah(temp_k: float, ah_gm3: float) -> float:
    """Compute RH fraction from temperature and absolute humidity."""
    Rv = 461.5
    e = (ah_gm3 / 1000.0) * Rv * temp_k  # g/m³ → Pa
    e_sat = _saturation_vapor_pressure(temp_k)
    if e_sat <= 0:
        return 0.0
    return e / e_sat


def _compensate_ah_for_temperature(
    ah_series: list[tuple[float, float]],
    temp_series: list[tuple[float, float]],
    baseline_temp: float,
    baseline_ah: float,
) -> list[tuple[float, float]]:
    """Remove temperature-induced AH changes from a humidity time series.

    For each sample, compute what the AH would be if the temperature
    had remained at baseline.  The difference between observed AH and
    this temperature-compensated AH is the device's contribution.

    Method:
    1. At baseline, compute %RH from baseline temp and baseline AH.
    2. For each sample, compute what AH would be at baseline temp
       given the observed %RH at the actual temperature.
    3. Wait, that's wrong — %RH changes when moisture is added.

    Correct method:
    1. For each sample, compute the expected AH if only temperature
       changed (constant water content = constant %RH at the moment).
    2. No — that's also wrong. We need to separate moisture addition
       from temperature effect.

    Actually the cleanest approach:
    1. At each timestep, compute what AH *would be* at baseline_temp
       given the observed AH at observed temp.  This is: convert
       observed AH to %RH at observed temp, then convert that %RH
       back to AH at baseline_temp.
    2. The difference between this temperature-normalized AH and the
       baseline AH is the device's moisture contribution.

    This works because: if temperature drops but no moisture is added
    or removed, %RH rises but the water content (mixing ratio) stays
    constant.  Converting to %RH at actual temp, then to AH at
    baseline temp, recovers the original AH — net zero.  Only actual
    moisture addition/removal shows up as a change.
    """
    # Build a temp lookup by nearest time
    temp_by_time = {}
    for t, v in temp_series:
        temp_by_time[t] = v

    compensated = []
    for t_ah, ah_obs in ah_series:
        # Find nearest temperature reading
        closest_t = min(temp_by_time.keys(), key=lambda t: abs(t - t_ah))
        temp_actual = temp_by_time[closest_t]

        # Convert observed AH to %RH at actual temperature
        rh_actual = _rh_from_temp_and_ah(temp_actual, ah_obs)

        # Convert that %RH back to AH at baseline temperature
        ah_at_baseline_temp = _ah_from_temp_and_rh(baseline_temp, rh_actual)

        compensated.append((t_ah, ah_at_baseline_temp))

    return compensated


# ── Recording engine ─────────────────────────────────────────────

class CalibrationEngine:
    """Records calibration data, then analyzes it."""

    # Recording thresholds
    MIN_DIFFERENTIAL_TEMP = 11.0    # K (~20°F)
    MIN_DIFFERENTIAL_AH = 1.5       # g/m³
    MAX_ACTIVATION_TIME = 1800      # 30 min
    RECOVERY_THRESHOLD_TEMP = 0.5   # K
    RECOVERY_THRESHOLD_AH = 0.3     # g/m³
    MAX_RECOVERY_TIME = 1800        # 30 min
    POWER_READ_DELAY = 15           # seconds after activation

    # Settle detection
    SETTLE_PATIENCE = 120
    SETTLE_WINDOW = 30
    SETTLE_THRESHOLD_TEMP = 0.2
    SETTLE_THRESHOLD_AH = 0.3

    # Preconditioning
    PRECONDITION_TEMP_THRESHOLD = 2.0   # K
    PRECONDITION_AH_THRESHOLD = 2.0     # g/m³ (loosened from 1.0)
    PRECONDITION_MAX_TIME = 600
    PRECONDITION_SETTLE_TIME = 30

    # Thermal warmup
    WARMUP_TEMP_THRESHOLD = 1.0     # K
    WARMUP_MAX_TIME = 1800

    # Passive measurement
    PASSIVE_MEASUREMENT_TIME = 600

    def __init__(self, cfg: dict, environment: str,
                 on_status: Any = None) -> None:
        self._cfg = cfg
        self._env_name = environment
        self._on_status = on_status or (lambda msg: None)
        self._sensors: dict = {}
        self._devices: dict = {}
        self._sensor_configs: dict = {}
        self._device_configs: dict = {}
        self._heater_cal: DeviceCalibration | None = None  # Loaded if available

    def _status(self, msg: str) -> None:
        log.info(msg)
        self._on_status(msg)

    # ── Sensor helpers ───────────────────────────────────────────

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
        fresh_mult = scfg.get("fresh_multiplier",
                              defaults.get("fresh_multiplier", 1.5))
        if classify_freshness(reading, interval, fresh_mult) != Freshness.FRESH:
            return None
        return reading

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

    def _sample(self, start_time: float) -> Sample | None:
        env = self._read_env()
        amb = self._read_ambient()
        if env is None:
            return None
        now = time.time()
        return Sample(
            elapsed=now - start_time,
            timestamp=now,
            env=env,
            ambient=amb or {},
        )

    # ── Setup ────────────────────────────────────────────────────

    def setup(self) -> bool:
        from spriggler.util.discovery import discover_plugins
        from spriggler.sensors import driver_registry as sensor_reg
        from spriggler.devices import driver_registry as device_reg

        discover_plugins(package="spriggler.physics")
        discover_plugins(package="spriggler.sensors",
                         exclude={"base", "freshness"})
        discover_plugins(package="spriggler.devices",
                         exclude={"kasa_mgr", "vesync_mgr"})

        for name, scfg in self._cfg.get("sensors", {}).items():
            env = scfg.get("environment")
            if env not in (self._env_name, "ambient"):
                continue
            drv_cls = sensor_reg.get(scfg.get("driver"))
            if drv_cls is None:
                continue
            try:
                self._sensors[name] = drv_cls(
                    sensor_name=name,
                    driver_config=scfg.get("driver_config", {}),
                )
                self._sensor_configs[name] = scfg
                self._status(f"Sensor ready: {name}")
            except Exception as e:
                self._status(f"Sensor {name} failed: {e}")

        for name, dcfg in self._cfg.get("devices", {}).items():
            if dcfg.get("environment") != self._env_name:
                continue
            drv_cls = device_reg.get(dcfg.get("driver"))
            if drv_cls is None:
                continue
            try:
                self._devices[name] = drv_cls(
                    device_name=name,
                    driver_config=dcfg.get("driver_config", {}),
                )
                self._device_configs[name] = dcfg
                self._status(f"Device ready: {name}")
            except Exception as e:
                self._status(f"Device {name} failed: {e}")

        # Load existing heater calibration if available
        self._load_heater_calibration()

        self._status("Waiting for sensor data...")
        time.sleep(5)
        deadline = time.time() + 30
        while time.time() < deadline:
            env = self._read_env()
            if env:
                self._status(
                    f"Sensors online: T={env.get('temperature', 0):.2f}K "
                    f"AH={env.get('absolute_humidity', 0):.2f}g/m³"
                )
                return True
            time.sleep(1)

        self._status("ERROR: No sensor data received")
        return False

    def _load_heater_calibration(self) -> None:
        """Load existing heater calibration for thermal hold."""
        home = self._cfg.get("_home")
        if home is None:
            self._status("No home path in config — cannot load calibration")
            return
        cal = load_calibration(self._env_name, Path(home))
        if cal is None:
            self._status(f"No existing calibration for {self._env_name}")
            return
        heater_name = self._find_heater_device()
        if heater_name and heater_name in cal.devices:
            self._heater_cal = cal.devices[heater_name]
            self._status(f"Loaded heater calibration: {heater_name}")
        elif heater_name:
            self._status(
                f"Calibration exists but no data for {heater_name} "
                f"(devices in cal: {list(cal.devices.keys())})"
            )

    def _get_heater_coast_overshoot(self) -> float | None:
        """Get the heater's coast overshoot from calibration data (in K)."""
        if self._heater_cal is None:
            return None
        for state, scal in self._heater_cal.states.items():
            temp_cal = scal.properties.get("temperature")
            if temp_cal:
                return temp_cal.coast_overshoot
        return None

    # ── Device finders ───────────────────────────────────────────

    def _find_transfer_device(self) -> str | None:
        for name, dcfg in self._device_configs.items():
            if dcfg.get("type") == "transfer":
                return name
        return None

    def _find_heater_device(self) -> str | None:
        for name, dcfg in self._device_configs.items():
            props = dcfg.get("intended_properties", {})
            if props.get("temperature") == "increase":
                return name
        return None

    def _get_target_temperature(self) -> float | None:
        schedules = self._cfg.get("schedules", {})
        env_schedule = schedules.get(self._env_name, {})
        phases = env_schedule.get("phases", [])
        for phase in phases:
            targets = phase.get("targets", {})
            temp_target = targets.get("temperature")
            if temp_target is not None:
                from spriggler.physics.temperature import fahrenheit_to_kelvin
                return fahrenheit_to_kelvin(float(temp_target))
        return None

    # ── Precondition dependency checking ─────────────────────────

    def _check_precondition_deps(self, intended_properties: dict) -> str | None:
        """Check whether required calibrations exist for preconditioning.

        Returns an error message if a dependency is missing, None if OK.
        """
        needs_thermal = "temperature" not in intended_properties

        if needs_thermal:
            heater_name = self._find_heater_device()
            if heater_name is None:
                return (
                    "No heater device configured — cannot create thermal "
                    "envelope for calibration.  Add a temperature-increasing "
                    "device to the config."
                )

            if self._heater_cal is None:
                return (
                    f"Heater '{heater_name}' has not been calibrated yet.  "
                    f"Calibrate the heater first to establish the thermal "
                    f"envelope:\n"
                    f"  spriggler calibrate run -e {self._env_name} -d {heater_name}"
                )

            coast = self._get_heater_coast_overshoot()
            if coast is None:
                return (
                    f"Heater calibration exists but has no temperature coast "
                    f"data.  Re-calibrate the heater:\n"
                    f"  spriggler calibrate run -e {self._env_name} -d {heater_name}"
                )

        return None

    # ── Preconditioning ──────────────────────────────────────────

    def _precondition(self, intended_properties: dict) -> bool:
        """Prepare environment for calibration.

        1. Fan flush toward ambient (universal reset)
        2. If non-temperature device: warm to operating temp using heater
        """
        fan_name = self._find_transfer_device()
        fan = self._devices.get(fan_name) if fan_name else None

        # Step 1: Fan flush
        if fan:
            self._status("Preconditioning: all devices OFF...")
            for name, device in self._devices.items():
                device.set_state("off")
            time.sleep(3)

            self._status(f"Preconditioning: {fan_name} ON — flushing toward ambient...")
            fan.set_state("on")

            deadline = time.time() + self.PRECONDITION_MAX_TIME
            while time.time() < deadline:
                env = self._read_env()
                amb = self._read_ambient()
                if env is None or amb is None:
                    time.sleep(3)
                    continue

                temp_diff = abs(env.get("temperature", 0) - amb.get("temperature", 0))
                ah_diff = abs(env.get("absolute_humidity", 0) - amb.get("absolute_humidity", 0))

                elapsed = self.PRECONDITION_MAX_TIME - (deadline - time.time())
                self._status(
                    f"  {elapsed:.0f}s: ΔT={temp_diff:.1f}K  ΔAH={ah_diff:.2f}g/m³"
                )

                if temp_diff < self.PRECONDITION_TEMP_THRESHOLD and \
                   ah_diff < self.PRECONDITION_AH_THRESHOLD:
                    self._status("Preconditioning: near ambient")
                    break

                time.sleep(5)

            fan.set_state("off")
            time.sleep(self.PRECONDITION_SETTLE_TIME)
        else:
            self._status("No transfer device — skipping fan flush")

        # Step 2: Thermal warmup if needed
        if "temperature" not in intended_properties:
            target_k = self._get_target_temperature()
            heater_name = self._find_heater_device()
            heater = self._devices.get(heater_name) if heater_name else None

            if target_k and heater:
                from spriggler.physics.temperature import kelvin_to_fahrenheit
                target_f = kelvin_to_fahrenheit(target_k)
                coast = self._get_heater_coast_overshoot() or 1.5

                # Turn off coast_overshoot before target so coast
                # carries us to target
                shutoff_k = target_k - coast
                shutoff_f = kelvin_to_fahrenheit(shutoff_k)

                self._status(
                    f"Preconditioning: warming to {target_f:.0f}°F "
                    f"(heater off at {shutoff_f:.0f}°F, coast {coast:.1f}K)..."
                )
                heater.set_state("on")

                deadline = time.time() + self.WARMUP_MAX_TIME
                while time.time() < deadline:
                    env = self._read_env()
                    if env is None:
                        time.sleep(3)
                        continue

                    current_temp = env.get("temperature", 0)
                    current_f = kelvin_to_fahrenheit(current_temp)
                    diff = target_k - current_temp

                    self._status(
                        f"  warming: {current_f:.1f}°F "
                        f"(target {target_f:.0f}°F, Δ={diff:+.1f}K)"
                    )

                    # Shut off at target - coast_overshoot
                    if current_temp >= shutoff_k:
                        self._status(
                            f"Preconditioning: heater OFF at {current_f:.1f}°F "
                            f"(coast will carry to ~{target_f:.0f}°F)"
                        )
                        break

                    time.sleep(5)

                heater.set_state("off")
                # Wait for coast to play out
                self._status("Preconditioning: waiting for thermal coast...")
                time.sleep(min(coast * 30, 120))  # Rough: coast_K * 30s
            else:
                if not target_k:
                    self._status("No target temperature in schedule — skipping warmup")
                if not heater:
                    self._status("No heater device — skipping warmup")

        return True

    # ── Thermal hold during recording ────────────────────────────

    def _thermal_hold_tick(self, heater, target_k: float,
                           coast_overshoot: float,
                           env: dict[str, float]) -> None:
        """One tick of bang-bang thermal hold.

        Uses heater calibration data to maintain temperature:
        - Heater ON when temp drops below target - coast_overshoot
        - Heater OFF when temp rises to target
        """
        current_temp = env.get("temperature", 0)
        if current_temp <= 0:
            return

        lower_bound = target_k - coast_overshoot
        if current_temp < lower_bound and not heater.last_commanded_state == "on":
            heater.set_state("on")
        elif current_temp >= target_k and heater.last_commanded_state == "on":
            heater.set_state("off")

    # ── Phase 1: Record ──────────────────────────────────────────

    def record_device_state(self, device_name: str,
                            state: str) -> Recording | None:
        """Record a complete activate → recover cycle."""
        device = self._devices.get(device_name)
        dcfg = self._device_configs.get(device_name)
        if device is None or dcfg is None:
            self._status(f"Device {device_name} not available")
            return None

        intended = _map_intended_properties(
            dcfg.get("intended_properties", {})
        )
        recording = Recording(device_name=device_name, state=state)

        # Check precondition dependencies
        dep_error = self._check_precondition_deps(intended)
        if dep_error:
            self._status(f"ERROR: {dep_error}")
            return None

        # Precondition
        self._precondition(intended)

        # Determine if we need thermal hold
        needs_thermal_hold = "temperature" not in intended
        heater = None
        target_k = None
        coast_overshoot = None

        if needs_thermal_hold:
            heater_name = self._find_heater_device()
            heater = self._devices.get(heater_name) if heater_name else None
            target_k = self._get_target_temperature()
            coast_overshoot = self._get_heater_coast_overshoot() or 1.5
            recording.thermal_hold_active = True
            self._status(
                f"Thermal hold active: target {target_k:.1f}K, "
                f"band ±{coast_overshoot:.1f}K"
            )

        # Ensure device OFF and settle
        self._status(f"Ensuring {device_name} OFF, waiting for settle...")
        device.set_state("off")
        time.sleep(5)

        baseline = self._wait_for_settle()
        if baseline is None:
            self._status("WARNING: Could not settle, using current readings")
            baseline = self._read_env()
            if baseline is None:
                self._status("ERROR: No sensor data")
                return None

        self._status(
            f"Baseline: T={baseline.get('temperature', 0):.2f}K "
            f"AH={baseline.get('absolute_humidity', 0):.2f}g/m³"
        )

        amb = self._read_ambient()
        if amb:
            recording.ambient_temp_at_start = amb.get("temperature")

        rec_start = time.time()

        # Baseline samples
        self._status("Recording baseline...")
        for _ in range(5):
            s = self._sample(rec_start)
            if s:
                recording.samples.append(s)
                if needs_thermal_hold and heater and target_k:
                    self._thermal_hold_tick(heater, target_k, coast_overshoot, s.env)
            time.sleep(3)

        # ── Activate ─────────────────────────────────────────────
        self._status(f"Activating {device_name} → {state}")
        device.set_state(state)
        recording.activation_index = len(recording.samples)
        activation_time = time.time()

        # Power reading
        power_draw = None
        if hasattr(device, "read_power"):
            self._status(f"Waiting {self.POWER_READ_DELAY}s for power to stabilize...")
            power_wait_end = time.time() + self.POWER_READ_DELAY
            while time.time() < power_wait_end:
                s = self._sample(rec_start)
                if s:
                    recording.samples.append(s)
                    if needs_thermal_hold and heater and target_k:
                        self._thermal_hold_tick(heater, target_k, coast_overshoot, s.env)
                time.sleep(3)
            power_draw = device.read_power()
            if power_draw is not None:
                self._status(f"Power draw: {power_draw:.1f}W")

        # Record activation
        self._status("Recording activation phase...")
        act_deadline = activation_time + self.MAX_ACTIVATION_TIME

        while time.time() < act_deadline:
            s = self._sample(rec_start)
            if s is None:
                time.sleep(3)
                continue

            s.power = power_draw
            recording.samples.append(s)

            # Thermal hold
            if needs_thermal_hold and heater and target_k:
                self._thermal_hold_tick(heater, target_k, coast_overshoot, s.env)

            # Check differential
            achieved = True
            for prop, direction in intended.items():
                current = s.env.get(prop)
                base = baseline.get(prop)
                if current is None or base is None:
                    achieved = False
                    continue

                delta = current - base
                if prop == "temperature":
                    threshold = self.MIN_DIFFERENTIAL_TEMP
                elif prop == "absolute_humidity":
                    threshold = self.MIN_DIFFERENTIAL_AH
                else:
                    threshold = 0.5

                if abs(delta) < threshold:
                    achieved = False

            # Status
            elapsed = time.time() - activation_time
            if len(recording.samples) % 5 == 0:
                for prop in intended:
                    current = s.env.get(prop)
                    base = baseline.get(prop)
                    if current is not None and base is not None:
                        delta = current - base
                        check = "✓" if abs(delta) >= threshold else ""
                        self._status(
                            f"  {elapsed:.0f}s: {prop} "
                            f"Δ={delta:+.4f} {check}"
                        )
                # Also show temperature during humidity calibration
                if needs_thermal_hold and "absolute_humidity" in intended:
                    temp = s.env.get("temperature")
                    if temp:
                        from spriggler.physics.temperature import kelvin_to_fahrenheit
                        temp_f = kelvin_to_fahrenheit(temp)
                        heater_state = heater.last_commanded_state if heater else "?"
                        self._status(
                            f"  {elapsed:.0f}s: temp hold {temp_f:.1f}°F "
                            f"[heater {heater_state}]"
                        )

            if achieved:
                self._status(f"Target differential achieved after {elapsed:.0f}s")
                break

            time.sleep(3)

        # ── Deactivate ───────────────────────────────────────────
        self._status(f"Deactivating {device_name} → off")
        device.set_state("off")
        recording.deactivation_index = len(recording.samples)

        # Turn off thermal hold heater
        if needs_thermal_hold and heater:
            heater.set_state("off")

        # ── Recovery ─────────────────────────────────────────────
        has_humidity = "absolute_humidity" in intended
        fan_name = self._find_transfer_device()
        fan = self._devices.get(fan_name) if fan_name else None
        fan_activated = False
        COAST_OBSERVE_TIME = 180

        self._status("Recording recovery phase (coast + decay)...")
        recovery_start = time.time()
        recovery_deadline = recovery_start + self.MAX_RECOVERY_TIME

        while time.time() < recovery_deadline:
            s = self._sample(rec_start)
            if s is None:
                time.sleep(3)
                continue

            recording.samples.append(s)
            elapsed_recovery = time.time() - recovery_start

            if has_humidity and fan and not fan_activated \
               and elapsed_recovery > COAST_OBSERVE_TIME:
                self._status(
                    f"Coast observed for {COAST_OBSERVE_TIME}s — "
                    f"activating {fan_name} for humidity recovery"
                )
                fan.set_state("on")
                fan_activated = True

            recovered = True
            for prop in intended:
                current = s.env.get(prop)
                base = baseline.get(prop)
                if current is None or base is None:
                    recovered = False
                    continue
                diff_from_base = abs(current - base)
                if prop == "temperature":
                    thr = self.RECOVERY_THRESHOLD_TEMP
                elif prop == "absolute_humidity":
                    thr = self.RECOVERY_THRESHOLD_AH
                else:
                    thr = 0.3
                if diff_from_base > thr:
                    recovered = False

            if len(recording.samples) % 5 == 0:
                for prop in intended:
                    current = s.env.get(prop)
                    base = baseline.get(prop)
                    if current is not None and base is not None:
                        delta = current - base
                        fan_tag = " [fan]" if fan_activated else ""
                        self._status(
                            f"  recovery {elapsed_recovery:.0f}s: "
                            f"{prop} Δ from baseline={delta:+.4f}{fan_tag}"
                        )

            if recovered:
                self._status(f"Recovered to baseline after {elapsed_recovery:.0f}s")
                break

            time.sleep(3)

        if fan_activated and fan:
            fan.set_state("off")
            time.sleep(5)

        self._status(
            f"Recording complete: {len(recording.samples)} samples "
            f"over {recording.samples[-1].elapsed:.0f}s"
        )
        return recording

    # ── Phase 2: Analyze ─────────────────────────────────────────

    def analyze_recording(self, recording: Recording,
                          intended_properties: dict) -> StateCalibration:
        """Analyze a recording to extract calibration parameters.

        For humidity properties, applies temperature compensation
        to remove thermal-induced AH changes.
        """
        samples = recording.samples
        act_idx = recording.activation_index
        deact_idx = recording.deactivation_index

        if deact_idx <= act_idx or len(samples) < 5:
            self._status("WARNING: Not enough data to analyze")
            return StateCalibration(state=recording.state)

        activation_samples = samples[act_idx:deact_idx]
        recovery_samples = samples[deact_idx:]

        # Power
        power_readings = [s.power for s in activation_samples
                          if s.power is not None]
        power_draw = None
        if power_readings:
            power_readings.sort()
            power_draw = power_readings[len(power_readings) // 2]

        state_cal = StateCalibration(
            state=recording.state,
            power_draw=power_draw,
        )

        # Average temperature during activation (for reference)
        act_temps = [s.env.get("temperature") for s in activation_samples
                     if "temperature" in s.env]
        if act_temps:
            state_cal.avg_temp_during_activation = sum(act_temps) / len(act_temps)

        act_start_time = activation_samples[0].elapsed if activation_samples else 0
        act_end_time = activation_samples[-1].elapsed if activation_samples else 0
        deact_time = recovery_samples[0].elapsed if recovery_samples else act_end_time

        # Get baseline values for compensation
        baseline_samples = samples[:act_idx]
        baseline_temp = None
        baseline_ah = None
        if baseline_samples:
            bt = [s.env.get("temperature") for s in baseline_samples
                  if "temperature" in s.env]
            bah = [s.env.get("absolute_humidity") for s in baseline_samples
                   if "absolute_humidity" in s.env]
            if bt:
                baseline_temp = sum(bt) / len(bt)
            if bah:
                baseline_ah = sum(bah) / len(bah)

        for prop, direction in intended_properties.items():
            act_points = [
                (s.elapsed - act_start_time, s.env.get(prop))
                for s in activation_samples if prop in s.env
            ]
            if len(act_points) < 2:
                continue

            # Temperature compensation for humidity
            rate_raw = self._linear_slope(act_points)
            rate = rate_raw

            if prop == "absolute_humidity" and recording.thermal_hold_active:
                # Get temperature series during activation
                temp_points = [
                    (s.elapsed - act_start_time, s.env.get("temperature"))
                    for s in activation_samples if "temperature" in s.env
                ]

                if baseline_temp and baseline_ah and len(temp_points) >= 2:
                    compensated = _compensate_ah_for_temperature(
                        act_points, temp_points,
                        baseline_temp, baseline_ah,
                    )
                    rate = self._linear_slope(compensated)
                    self._status(
                        f"  AH compensation: raw rate={rate_raw*60:+.4f} g/m³/min "
                        f"→ compensated={rate*60:+.4f} g/m³/min"
                    )

            # Coast analysis
            rec_points = [
                (s.elapsed - deact_time, s.env.get(prop))
                for s in recovery_samples if prop in s.env
            ]

            coast_overshoot = 0.0
            coast_duration = 0.0
            coast_profile = []

            if rec_points:
                deact_value = act_points[-1][1] if act_points else 0

                if direction == "increase":
                    peak_val = deact_value
                    peak_time = 0.0
                    for t, v in rec_points:
                        if v is not None and v > peak_val:
                            peak_val = v
                            peak_time = t
                    coast_overshoot = peak_val - deact_value
                else:
                    peak_val = deact_value
                    peak_time = 0.0
                    for t, v in rec_points:
                        if v is not None and v < peak_val:
                            peak_val = v
                            peak_time = t
                    coast_overshoot = peak_val - deact_value

                coast_duration = peak_time
                coast_profile = [(t, v) for t, v in rec_points
                                 if v is not None]

            prop_cal = PropertyCalibration(
                property_name=prop,
                rate=rate,
                rate_raw=rate_raw if rate != rate_raw else None,
                coast_overshoot=coast_overshoot,
                coast_duration=coast_duration,
                coast_profile=coast_profile,
            )
            state_cal.properties[prop] = prop_cal

            if prop == "temperature":
                rate_fpm = rate * 9/5 * 60
                coast_f = coast_overshoot * 9/5
                self._status(
                    f"  {prop}: rate={rate_fpm:+.3f}°F/min  "
                    f"coast={coast_f:+.3f}°F over {coast_duration:.0f}s"
                )
            elif prop == "absolute_humidity":
                self._status(
                    f"  {prop}: rate={rate*60:+.4f} g/m³/min  "
                    f"coast={coast_overshoot:+.4f} g/m³ over "
                    f"{coast_duration:.0f}s"
                )

        # Thermal byproduct
        if "temperature" not in intended_properties and activation_samples:
            temp_points = [
                (s.elapsed - act_start_time, s.env.get("temperature"))
                for s in activation_samples if "temperature" in s.env
            ]
            if len(temp_points) >= 2:
                temp_rate = self._linear_slope(temp_points)
                if abs(temp_rate) > 0.0001:
                    state_cal.thermal_byproduct_rate = temp_rate
                    byproduct_fpm = temp_rate * 9/5 * 60
                    self._status(
                        f"  thermal byproduct: {byproduct_fpm:+.3f}°F/min"
                    )

        return state_cal

    @staticmethod
    def _linear_slope(points: list[tuple[float, float | None]]) -> float:
        clean = [(t, v) for t, v in points if v is not None]
        n = len(clean)
        if n < 2:
            return 0.0
        sum_t = sum(t for t, _ in clean)
        sum_v = sum(v for _, v in clean)
        sum_tt = sum(t * t for t, _ in clean)
        sum_tv = sum(t * v for t, v in clean)
        denom = n * sum_tt - sum_t * sum_t
        if abs(denom) < 1e-12:
            return 0.0
        return (n * sum_tv - sum_t * sum_v) / denom

    def _wait_for_settle(self) -> dict[str, float] | None:
        history: list[tuple[float, dict[str, float]]] = []
        deadline = time.time() + self.SETTLE_PATIENCE
        while time.time() < deadline:
            values = self._read_env()
            if values is None:
                time.sleep(3)
                continue
            now = time.time()
            history.append((now, values))
            cutoff = now - self.SETTLE_WINDOW
            history = [(t, v) for t, v in history if t >= cutoff]
            if len(history) < 3:
                time.sleep(3)
                continue
            temps = [v.get("temperature") for _, v in history
                     if "temperature" in v]
            ahs = [v.get("absolute_humidity") for _, v in history
                   if "absolute_humidity" in v]
            temp_ok = len(temps) < 2 or (max(temps) - min(temps)) < self.SETTLE_THRESHOLD_TEMP
            ah_ok = len(ahs) < 2 or (max(ahs) - min(ahs)) < self.SETTLE_THRESHOLD_AH
            if temp_ok and ah_ok:
                return values
            time.sleep(3)
        return None

    # ── Passive conductance ──────────────────────────────────────

    def record_passive(self) -> list[Sample]:
        self._status("\nRecording passive decay...")
        self._status("Ensuring all devices OFF...")
        for name, device in self._devices.items():
            device.set_state("off")
        time.sleep(5)

        self._status(f"Recording for {self.PASSIVE_MEASUREMENT_TIME}s...")
        samples: list[Sample] = []
        start = time.time()

        while time.time() - start < self.PASSIVE_MEASUREMENT_TIME:
            s = self._sample(start)
            if s:
                samples.append(s)
                if len(samples) % 10 == 0:
                    amb_temp = s.ambient.get("temperature")
                    amb_str = f"{amb_temp:.2f}K" if amb_temp else "—"
                    self._status(
                        f"  {s.elapsed:.0f}s: "
                        f"T={s.env.get('temperature', 0):.2f}K "
                        f"amb={amb_str}"
                    )
            time.sleep(5)

        self._status(f"Passive recording: {len(samples)} samples")
        return samples

    def analyze_passive(self, samples: list[Sample]) -> dict:
        if len(samples) < 5:
            self._status("ERROR: Not enough passive data")
            return {}

        result = {}
        for prop in ("temperature", "absolute_humidity"):
            env_pts = []
            amb_pts = []
            for s in samples:
                env_val = s.env.get(prop)
                amb_val = s.ambient.get(prop)
                if env_val is not None and amb_val is not None and amb_val > 0:
                    env_pts.append((s.elapsed, env_val))
                    amb_pts.append((s.elapsed, amb_val))

            if len(env_pts) < 3 or len(amb_pts) < 3:
                self._status(
                    f"  {prop}: not enough paired env/ambient readings "
                    f"({len(env_pts)} pairs), skipping"
                )
                continue

            avg_ambient = sum(v for _, v in amb_pts) / len(amb_pts)
            rate = self._linear_slope(env_pts)
            avg_env = sum(v for _, v in env_pts) / len(env_pts)
            differential = avg_ambient - avg_env

            if abs(differential) < 0.1:
                self._status(
                    f"  {prop}: differential too small "
                    f"({differential:.2f}), skipping"
                )
                continue

            g_passive = rate / differential
            tau = 1.0 / g_passive if g_passive != 0 else float("inf")

            result[prop] = {
                "g_passive": g_passive,
                "tau": tau,
                "rate": rate,
                "differential": differential,
            }
            self._status(
                f"  {prop}: g_passive={g_passive:.6f}/s  "
                f"τ={tau:.0f}s ({tau/60:.1f}min)  "
                f"diff={differential:+.2f}"
            )

        return result

    # ── Full run ─────────────────────────────────────────────────

    def run(self, device_names: list[str] | None = None,
            include_passive: bool = True) -> EnvironmentCalibration:

        # Start from existing calibration if available, so we don't
        # lose data from previous device calibrations when running
        # a single device (e.g., calibrating humidifier shouldn't
        # wipe out the heater calibration)
        home = self._cfg.get("_home")
        env_cal = None
        if home:
            env_cal = load_calibration(self._env_name, Path(home))
        if env_cal is None:
            env_cal = EnvironmentCalibration(environment_name=self._env_name)

        targets = device_names or list(self._devices.keys())
        calibratable = []
        for name in targets:
            dcfg = self._device_configs.get(name, {})
            if dcfg.get("type") == "transfer":
                self._status(f"Skipping {name} (transfer device)")
                continue
            calibratable.append(name)

        self._status(f"\nCalibrating {len(calibratable)} device(s)")

        for name in calibratable:
            dcfg = self._device_configs[name]
            intended = _map_intended_properties(
                dcfg.get("intended_properties", {})
            )
            device = self._devices[name]
            states = device.get_states()
            active_states = [s for s in states if s != "off"]

            dcal = DeviceCalibration(device_name=name)
            amb = self._read_ambient()
            if amb:
                dcal.ambient_temp_during_cal = amb.get("temperature")

            for state in active_states:
                self._status(f"\n{'='*50}")
                self._status(f"Recording {name} state: {state}")
                self._status(f"{'='*50}")

                recording = self.record_device_state(name, state)
                if recording is None:
                    continue

                self._status(f"\nAnalyzing {name} / {state}...")
                state_cal = self.analyze_recording(recording, intended)
                dcal.states[state] = state_cal

            dcal.calibrated_at = time.time()
            env_cal.devices[name] = dcal

        if include_passive:
            passive_samples = self.record_passive()
            passive_result = self.analyze_passive(passive_samples)
            for prop, data in passive_result.items():
                env_cal.passive_conductance[prop] = data["g_passive"]
                env_cal.time_constant[prop] = data["tau"]

        env_cal.calibrated_at = time.time()
        return env_cal


# ── Serialization ────────────────────────────────────────────────

def save_calibration(cal: EnvironmentCalibration, home: Path) -> Path:
    cal_dir = home / "calibration"
    cal_dir.mkdir(parents=True, exist_ok=True)
    filepath = cal_dir / f"{cal.environment_name}.json"
    data = _cal_to_dict(cal)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    log.info("Calibration saved: %s", filepath)
    return filepath


def load_calibration(environment: str, home: Path) -> EnvironmentCalibration | None:
    filepath = home / "calibration" / f"{environment}.json"
    if not filepath.is_file():
        return None
    with open(filepath) as f:
        data = json.load(f)
    return _dict_to_cal(data)


def _cal_to_dict(cal: EnvironmentCalibration) -> dict:
    return {
        "environment": cal.environment_name,
        "calibrated_at": cal.calibrated_at,
        "passive_conductance": cal.passive_conductance,
        "time_constant": cal.time_constant,
        "devices": {
            name: {
                "calibrated_at": dcal.calibrated_at,
                "ambient_temp": dcal.ambient_temp_during_cal,
                "states": {
                    state: {
                        "power_draw": scal.power_draw,
                        "thermal_byproduct_rate": scal.thermal_byproduct_rate,
                        "avg_temp_during_activation": scal.avg_temp_during_activation,
                        "properties": {
                            prop: {
                                "rate": pcal.rate,
                                "rate_raw": pcal.rate_raw,
                                "coast_overshoot": pcal.coast_overshoot,
                                "coast_duration": pcal.coast_duration,
                                "coast_profile": pcal.coast_profile,
                            }
                            for prop, pcal in scal.properties.items()
                        },
                    }
                    for state, scal in dcal.states.items()
                },
            }
            for name, dcal in cal.devices.items()
        },
    }


def _dict_to_cal(data: dict) -> EnvironmentCalibration:
    cal = EnvironmentCalibration(
        environment_name=data["environment"],
        calibrated_at=data.get("calibrated_at", 0),
        passive_conductance=data.get("passive_conductance", {}),
        time_constant=data.get("time_constant", {}),
    )
    for name, ddata in data.get("devices", {}).items():
        dcal = DeviceCalibration(
            device_name=name,
            calibrated_at=ddata.get("calibrated_at", 0),
            ambient_temp_during_cal=ddata.get("ambient_temp"),
        )
        for state, sdata in ddata.get("states", {}).items():
            scal = StateCalibration(
                state=state,
                power_draw=sdata.get("power_draw"),
                thermal_byproduct_rate=sdata.get("thermal_byproduct_rate"),
                avg_temp_during_activation=sdata.get("avg_temp_during_activation"),
            )
            for prop, pdata in sdata.get("properties", {}).items():
                pcal = PropertyCalibration(
                    property_name=prop,
                    rate=pdata["rate"],
                    rate_raw=pdata.get("rate_raw"),
                    coast_overshoot=pdata["coast_overshoot"],
                    coast_duration=pdata["coast_duration"],
                    coast_profile=pdata.get("coast_profile", []),
                )
                scal.properties[prop] = pcal
            dcal.states[state] = scal
        cal.devices[name] = dcal
    return cal
