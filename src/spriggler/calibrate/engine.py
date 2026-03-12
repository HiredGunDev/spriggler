"""Calibration engine — record everything, analyze after.

The calibration process is split into two clean phases:

RECORD:
    Turn device on.  Record every sensor reading (pod + ambient)
    with timestamps.  Wait for a solid temperature differential.
    Turn device off.  Keep recording until we return near baseline
    or ambient.  The ONLY decision during recording is "have we
    collected enough data?" — a simple differential check.

ANALYZE:
    Take the complete time series and extract:
      - Activation rate (slope during device-on phase)
      - Coast profile (trajectory after shutoff)
      - Coast overshoot (total change after shutoff)
      - Passive decay rate (slope after coast flattens)
      - Thermal byproduct (temperature change when temp isn't intended)

    All analysis is offline on clean data.  No real-time guessing.

This is how real instrumentation works.  Sample, then analyze.
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
class RecordingPhase:
    """Metadata about a phase boundary in the recording."""
    name: str           # "baseline", "activation", "recovery"
    start_index: int    # Index into samples list
    end_index: int      # Index into samples list


@dataclass
class Recording:
    """Complete recording of a calibration run for one device state."""
    device_name: str
    state: str
    samples: list[Sample] = field(default_factory=list)
    activation_index: int = 0     # Sample index where device was turned ON
    deactivation_index: int = 0   # Sample index where device was turned OFF
    ambient_temp_at_start: float | None = None


@dataclass
class PropertyCalibration:
    """Analyzed calibration for one property of one device state."""
    property_name: str
    rate: float                 # units/second during activation
    coast_overshoot: float      # total change after shutoff
    coast_duration: float       # seconds of meaningful coast
    coast_profile: list[tuple[float, float]]  # [(elapsed_since_off, value)]


@dataclass
class StateCalibration:
    """Analyzed calibration for one device state."""
    state: str
    properties: dict[str, PropertyCalibration] = field(default_factory=dict)
    thermal_byproduct_rate: float | None = None
    power_draw: float | None = None


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


# ── Recording engine ─────────────────────────────────────────────

class CalibrationEngine:
    """Records calibration data, then analyzes it.

    Parameters
    ----------
    cfg : dict
        Loaded config.
    environment : str
        Environment name to calibrate.
    on_status : callable, optional
        Status callback: on_status(message: str).
    """

    # Recording thresholds — these determine data quality.
    # Calibration runs once, controls run forever.  Don't skimp.
    #
    # Temperature: 11K ≈ 20°F.  With the Govee's 0.1°C resolution,
    # that's 110 sensor steps — excellent signal-to-noise ratio for
    # least-squares regression.  HVAC best practice: minimum 14-20°F
    # differential for meaningful measurement.
    #
    # Humidity: 3 g/m³ gives similar data quality proportional to
    # the sensor's resolution.
    MIN_DIFFERENTIAL_TEMP = 11.0    # K (~20°F) before we stop activation
    MIN_DIFFERENTIAL_AH = 3.0      # g/m³ before we stop activation
    MAX_ACTIVATION_TIME = 1800     # 30 min safety cap (20°F rise takes time)
    RECOVERY_THRESHOLD_TEMP = 0.5   # K — within this of baseline = recovered
    RECOVERY_THRESHOLD_AH = 0.3     # g/m³
    MAX_RECOVERY_TIME = 1800       # 30 min cap on recovery recording
    POWER_READ_DELAY = 15           # seconds after activation before reading power

    # Settle detection
    SETTLE_PATIENCE = 120
    SETTLE_WINDOW = 30
    SETTLE_THRESHOLD_TEMP = 0.2
    SETTLE_THRESHOLD_AH = 0.3

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
        """Take one complete sample (env + ambient)."""
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

        # Instantiate sensors
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

        # Instantiate devices
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

        # Wait for first reading
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

    # ── Phase 1: Record ──────────────────────────────────────────

    def record_device_state(self, device_name: str,
                            state: str) -> Recording | None:
        """Record a complete activate → recover cycle for one state.

        Returns the raw recording for later analysis.
        """
        device = self._devices.get(device_name)
        dcfg = self._device_configs.get(device_name)
        if device is None or dcfg is None:
            self._status(f"Device {device_name} not available")
            return None

        intended = dcfg.get("intended_properties", {})
        recording = Recording(device_name=device_name, state=state)

        # ── Ensure OFF and settle ────────────────────────────────
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

        # Start recording
        rec_start = time.time()

        # Record a few baseline samples
        self._status("Recording baseline...")
        for _ in range(5):
            s = self._sample(rec_start)
            if s:
                recording.samples.append(s)
            time.sleep(3)

        # ── Activate ─────────────────────────────────────────────
        self._status(f"Activating {device_name} → {state}")
        device.set_state(state)
        recording.activation_index = len(recording.samples)
        activation_time = time.time()

        # Read power after stabilization
        power_draw = None
        if hasattr(device, "read_power"):
            self._status(f"Waiting {self.POWER_READ_DELAY}s for power to stabilize...")
            # Keep sampling during the wait
            power_wait_end = time.time() + self.POWER_READ_DELAY
            while time.time() < power_wait_end:
                s = self._sample(rec_start)
                if s:
                    recording.samples.append(s)
                time.sleep(3)
            power_draw = device.read_power()
            if power_draw is not None:
                self._status(f"Power draw: {power_draw:.1f}W")

        # Record activation phase — until meaningful differential
        self._status("Recording activation phase...")
        act_deadline = activation_time + self.MAX_ACTIVATION_TIME

        while time.time() < act_deadline:
            s = self._sample(rec_start)
            if s is None:
                time.sleep(3)
                continue

            s.power = power_draw
            recording.samples.append(s)

            # Check differential from baseline
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

            # Status every ~15s
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

            if achieved:
                self._status(
                    f"Target differential achieved after {elapsed:.0f}s"
                )
                break

            time.sleep(3)

        # ── Deactivate ───────────────────────────────────────────
        self._status(f"Deactivating {device_name} → off")
        device.set_state("off")
        recording.deactivation_index = len(recording.samples)

        # ── Record recovery — until near baseline or ambient ─────
        self._status("Recording recovery phase (coast + decay)...")
        recovery_start = time.time()
        recovery_deadline = recovery_start + self.MAX_RECOVERY_TIME

        while time.time() < recovery_deadline:
            s = self._sample(rec_start)
            if s is None:
                time.sleep(3)
                continue

            recording.samples.append(s)

            # Check if recovered: near baseline for all intended props
            recovered = True
            for prop in intended:
                current = s.env.get(prop)
                base = baseline.get(prop)
                amb_val = s.ambient.get(prop)

                if current is None or base is None:
                    recovered = False
                    continue

                # "Recovered" = within threshold of baseline, OR
                # we've passed baseline heading toward ambient
                # (ambient might be colder than where we started)
                diff_from_base = abs(current - base)

                if prop == "temperature":
                    threshold = self.RECOVERY_THRESHOLD_TEMP
                elif prop == "absolute_humidity":
                    threshold = self.RECOVERY_THRESHOLD_AH
                else:
                    threshold = 0.3

                if diff_from_base > threshold:
                    recovered = False

            elapsed_recovery = time.time() - recovery_start
            if len(recording.samples) % 5 == 0:
                for prop in intended:
                    current = s.env.get(prop)
                    base = baseline.get(prop)
                    if current is not None and base is not None:
                        delta = current - base
                        self._status(
                            f"  recovery {elapsed_recovery:.0f}s: "
                            f"{prop} Δ from baseline={delta:+.4f}"
                        )

            if recovered:
                self._status(
                    f"Recovered to baseline after {elapsed_recovery:.0f}s"
                )
                break

            time.sleep(3)

        self._status(
            f"Recording complete: {len(recording.samples)} samples "
            f"over {recording.samples[-1].elapsed:.0f}s"
        )
        return recording

    # ── Phase 2: Analyze ─────────────────────────────────────────

    def analyze_recording(self, recording: Recording,
                          intended_properties: dict) -> StateCalibration:
        """Analyze a recording to extract calibration parameters.

        Parameters
        ----------
        recording : Recording
            The raw time-series data.
        intended_properties : dict
            Property name → direction from config.

        Returns
        -------
        StateCalibration
        """
        samples = recording.samples
        act_idx = recording.activation_index
        deact_idx = recording.deactivation_index

        if deact_idx <= act_idx or len(samples) < 5:
            self._status("WARNING: Not enough data to analyze")
            return StateCalibration(state=recording.state)

        # Extract phase slices
        activation_samples = samples[act_idx:deact_idx]
        recovery_samples = samples[deact_idx:]

        # Power: median of readings during activation (avoids inrush outliers)
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

        # Time spans
        act_start_time = activation_samples[0].elapsed if activation_samples else 0
        act_end_time = activation_samples[-1].elapsed if activation_samples else 0
        act_duration = act_end_time - act_start_time

        deact_time = recovery_samples[0].elapsed if recovery_samples else act_end_time

        for prop, direction in intended_properties.items():
            # Activation rate: linear slope during activation phase
            act_points = [
                (s.elapsed - act_start_time, s.env.get(prop))
                for s in activation_samples if prop in s.env
            ]
            if len(act_points) < 2:
                continue

            # Simple linear regression for rate
            rate = self._linear_slope(act_points)

            # Coast analysis: what happened after shutoff
            rec_points = [
                (s.elapsed - deact_time, s.env.get(prop))
                for s in recovery_samples if prop in s.env
            ]

            coast_overshoot = 0.0
            coast_duration = 0.0
            coast_profile = []

            if rec_points:
                deact_value = act_points[-1][1] if act_points else 0

                # Find the peak (or trough for decrease) after shutoff
                # This is where coast ends and decay begins
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
                coast_overshoot=coast_overshoot,
                coast_duration=coast_duration,
                coast_profile=coast_profile,
            )
            state_cal.properties[prop] = prop_cal

            # Display-friendly rate
            if prop == "temperature":
                rate_fpm = rate * 9/5 * 60
                coast_f = coast_overshoot * 9/5
                self._status(
                    f"  {prop}: rate={rate_fpm:+.3f}°F/min  "
                    f"coast={coast_f:+.3f}°F over {coast_duration:.0f}s"
                )
            elif prop == "absolute_humidity":
                rate_mpm = rate * 60
                self._status(
                    f"  {prop}: rate={rate_mpm:+.4f} g/m³/min  "
                    f"coast={coast_overshoot:+.4f} g/m³ over "
                    f"{coast_duration:.0f}s"
                )
            else:
                self._status(
                    f"  {prop}: rate={rate:.6f}/s  "
                    f"coast={coast_overshoot:+.4f}"
                )

        # Thermal byproduct
        if "temperature" not in intended_properties and activation_samples:
            temps = [s.env.get("temperature") for s in activation_samples
                     if "temperature" in s.env]
            if len(temps) >= 2:
                temp_points = [
                    (s.elapsed - act_start_time, s.env["temperature"])
                    for s in activation_samples if "temperature" in s.env
                ]
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
        """Compute slope via least-squares linear regression.

        Points are [(time, value)].  None values are skipped.
        Returns slope in value/second.
        """
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
        """Record passive decay — all devices OFF."""
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
        """Analyze passive decay recording."""
        if len(samples) < 5:
            self._status("ERROR: Not enough passive data")
            return {}

        result = {}
        for prop in ("temperature", "absolute_humidity"):
            # Only include samples where we have BOTH env and ambient
            # for this property, and both are real values (not None/0)
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
        """Run full calibration: record + analyze for each device."""

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
            intended = dcfg.get("intended_properties", {})
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

                # Record
                recording = self.record_device_state(name, state)
                if recording is None:
                    continue

                # Analyze
                self._status(f"\nAnalyzing {name} / {state}...")
                state_cal = self.analyze_recording(recording, intended)
                dcal.states[state] = state_cal

            dcal.calibrated_at = time.time()
            env_cal.devices[name] = dcal

        # Passive
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
                        "properties": {
                            prop: {
                                "rate": pcal.rate,
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
            )
            for prop, pdata in sdata.get("properties", {}).items():
                pcal = PropertyCalibration(
                    property_name=prop,
                    rate=pdata["rate"],
                    coast_overshoot=pdata["coast_overshoot"],
                    coast_duration=pdata["coast_duration"],
                    coast_profile=pdata.get("coast_profile", []),
                )
                scal.properties[prop] = pcal
            dcal.states[state] = scal
        cal.devices[name] = dcal
    return cal
