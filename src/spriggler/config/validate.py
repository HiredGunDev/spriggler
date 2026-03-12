"""Config validator — three-tier validation of Spriggler configuration.

Tier 1 — Schema:    Required sections, fields, and types.
Tier 2 — Semantic:  Cross-references between sections resolve correctly.
Tier 3 — Physics:   Logical/physical sanity checks.

Each check produces either an error (blocks operation) or a warning
(operational but suboptimal).  With --strict, warnings become errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Collects errors and warnings from validation."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def ok_strict(self) -> bool:
        return len(self.errors) == 0 and len(self.warnings) == 0


def validate_config(cfg: dict) -> ValidationResult:
    """Run all validation tiers on a loaded config.

    Parameters
    ----------
    cfg : dict
        Loaded, resolved config from load_config().

    Returns
    -------
    ValidationResult
        Collected errors and warnings.
    """
    result = ValidationResult()
    _validate_schema(cfg, result)
    # Only run semantic/physics if schema is clean enough
    if result.ok:
        _validate_semantic(cfg, result)
        _validate_physics(cfg, result)
    return result


# ── Tier 1: Schema ───────────────────────────────────────────────

def _validate_schema(cfg: dict, r: ValidationResult) -> None:
    """Check required sections, fields, and types."""

    # [meta]
    meta = cfg.get("meta")
    if not meta:
        r.error("[meta] section is required")
    else:
        if "version" not in meta:
            r.error("[meta] missing 'version'")
        if "name" not in meta:
            r.warn("[meta] missing 'name' (recommended)")

    # [environments]
    envs = cfg.get("environments")
    if not envs:
        r.error("[environments] section is required (need at least one)")
    elif not isinstance(envs, dict):
        r.error("[environments] must be a table of named environments")
    else:
        for name, env in envs.items():
            if not isinstance(env, dict):
                r.error(f"[environments.{name}] must be a table")
                continue
            if "media" not in env and env.get("controlled", True):
                r.error(f"[environments.{name}] missing 'media' (required for controlled environments)")

    # [sensors]
    sensors = cfg.get("sensors")
    if not sensors:
        r.error("[sensors] section is required (need at least one)")
    elif not isinstance(sensors, dict):
        r.error("[sensors] must be a table of named sensors")
    else:
        for name, sensor in sensors.items():
            if not isinstance(sensor, dict):
                r.error(f"[sensors.{name}] must be a table")
                continue
            for req in ("driver", "environment", "reports", "delivery_interval_seconds"):
                if req not in sensor:
                    r.error(f"[sensors.{name}] missing '{req}'")
            if "reports" in sensor and not isinstance(sensor["reports"], list):
                r.error(f"[sensors.{name}] 'reports' must be a list")
            if "delivery_interval_seconds" in sensor:
                interval = sensor["delivery_interval_seconds"]
                if not isinstance(interval, (int, float)) or interval <= 0:
                    r.error(f"[sensors.{name}] 'delivery_interval_seconds' must be a positive number")
            if "driver_config" not in sensor:
                r.warn(f"[sensors.{name}] missing 'driver_config' (most drivers need it)")

    # [devices]
    devices = cfg.get("devices")
    if not devices:
        r.warn("[devices] section is empty (nothing to control)")
    elif not isinstance(devices, dict):
        r.error("[devices] must be a table of named devices")
    else:
        valid_types = {"energy", "transfer"}
        valid_directions = {"increase", "decrease"}
        for name, dev in devices.items():
            if not isinstance(dev, dict):
                r.error(f"[devices.{name}] must be a table")
                continue
            for req in ("driver", "type", "environment"):
                if req not in dev:
                    r.error(f"[devices.{name}] missing '{req}'")
            dev_type = dev.get("type")
            if dev_type and dev_type not in valid_types:
                r.error(f"[devices.{name}] type '{dev_type}' invalid (must be: {', '.join(valid_types)})")
            # Validate intended_properties directions
            props = dev.get("intended_properties", {})
            if isinstance(props, dict):
                for prop, direction in props.items():
                    if direction not in valid_directions:
                        r.error(
                            f"[devices.{name}.intended_properties] "
                            f"'{prop}' direction '{direction}' invalid "
                            f"(must be: {', '.join(valid_directions)})"
                        )
            # Validate states list
            states = dev.get("states")
            if states is not None:
                if not isinstance(states, list) or len(states) < 2:
                    r.error(f"[devices.{name}] 'states' must be a list with at least 2 entries (including 'off')")
                elif "off" not in states:
                    r.error(f"[devices.{name}] 'states' must include 'off'")

    # [connections]
    connections = cfg.get("connections")
    if connections and isinstance(connections, dict):
        for name, conn in connections.items():
            if not isinstance(conn, dict):
                r.error(f"[connections.{name}] must be a table")
                continue
            endpoints = conn.get("endpoints")
            if not endpoints:
                r.error(f"[connections.{name}] missing 'endpoints'")
            elif not isinstance(endpoints, list) or len(endpoints) != 2:
                r.error(f"[connections.{name}] 'endpoints' must be a list of exactly 2 environment names")
            if "medium" not in conn:
                r.error(f"[connections.{name}] missing 'medium'")

    # [circuits]
    circuits = cfg.get("circuits")
    if circuits and isinstance(circuits, dict):
        for name, circuit in circuits.items():
            if not isinstance(circuit, dict):
                r.error(f"[circuits.{name}] must be a table")
                continue
            for req in ("max_amps", "voltage"):
                if req not in circuit:
                    r.error(f"[circuits.{name}] missing '{req}'")

    # [schedules]
    schedules = cfg.get("schedules")
    if schedules and isinstance(schedules, dict):
        for env_name, sched in schedules.items():
            phases = sched.get("phases")
            if not phases:
                r.warn(f"[schedules.{env_name}] has no phases")
            elif isinstance(phases, list):
                for i, phase in enumerate(phases):
                    if "name" not in phase:
                        r.warn(f"[schedules.{env_name}.phases[{i}]] missing 'name'")
                    for req in ("start", "end"):
                        if req not in phase:
                            r.error(f"[schedules.{env_name}.phases[{i}]] missing '{req}'")
                    if "targets" not in phase:
                        r.warn(f"[schedules.{env_name}.phases[{i}]] has no targets")

    # [safety]
    safety = cfg.get("safety")
    if not safety:
        r.warn("[safety] section is missing (recommended for production)")
    elif isinstance(safety, dict):
        if "default_bands" not in safety:
            r.warn("[safety] missing 'default_bands' (needed for pre-calibration control)")


# ── Tier 2: Semantic ─────────────────────────────────────────────

def _validate_semantic(cfg: dict, r: ValidationResult) -> None:
    """Check cross-references between sections."""

    env_names = set(cfg.get("environments", {}).keys())
    device_names = set(cfg.get("devices", {}).keys())
    sensor_names = set(cfg.get("sensors", {}).keys())
    circuit_names = set(cfg.get("circuits", {}).keys())

    # Sensors reference valid environments
    for name, sensor in cfg.get("sensors", {}).items():
        env = sensor.get("environment")
        if env and env not in env_names:
            r.error(f"[sensors.{name}] references unknown environment '{env}'")

    # Devices reference valid environments and circuits
    for name, dev in cfg.get("devices", {}).items():
        env = dev.get("environment")
        if env and env not in env_names:
            r.error(f"[devices.{name}] references unknown environment '{env}'")
        circuit = dev.get("circuit")
        if circuit and circuit not in circuit_names:
            r.error(f"[devices.{name}] references unknown circuit '{circuit}'")

    # Connections reference valid environments and devices
    for name, conn in cfg.get("connections", {}).items():
        for ep in conn.get("endpoints", []):
            if ep not in env_names:
                r.error(f"[connections.{name}] endpoint '{ep}' is not a known environment")
        td = conn.get("transfer_device")
        if td and td not in device_names:
            r.error(f"[connections.{name}] transfer_device '{td}' is not a known device")

    # Schedules reference valid environments and devices
    for env_name, sched in cfg.get("schedules", {}).items():
        if env_name not in env_names:
            r.error(f"[schedules.{env_name}] references unknown environment '{env_name}'")
        for i, phase in enumerate(sched.get("phases", [])):
            for dev_name in phase.get("devices", {}):
                if dev_name not in device_names:
                    r.error(
                        f"[schedules.{env_name}.phases[{i}]] "
                        f"references unknown device '{dev_name}'"
                    )

    # Safety devices reference valid devices
    for dev_name in cfg.get("safety", {}).get("devices", {}):
        if dev_name not in device_names:
            r.error(f"[safety.devices.{dev_name}] references unknown device '{dev_name}'")

    # Safety environments reference valid environments
    for env_name in cfg.get("safety", {}).get("environments", {}):
        if env_name not in env_names:
            r.error(f"[safety.environments.{env_name}] references unknown environment '{env_name}'")

    # Power monitoring plug_map references valid devices
    for strip_name, strip_data in cfg.get("power_monitoring", {}).items():
        for plug, dev_name in strip_data.get("plug_map", {}).items():
            if dev_name not in device_names:
                r.error(
                    f"[power_monitoring.{strip_name}.plug_map] "
                    f"plug '{plug}' maps to unknown device '{dev_name}'"
                )

    # ── Coverage warnings ────────────────────────────────────────

    # Controlled environments should have at least one sensor
    for env_name, env in cfg.get("environments", {}).items():
        if not env.get("controlled", True):
            continue
        has_sensor = any(
            s.get("environment") == env_name
            for s in cfg.get("sensors", {}).values()
        )
        if not has_sensor:
            r.warn(f"Environment '{env_name}' has no sensors (unobservable)")

    # Controlled environments should have at least one device
    for env_name, env in cfg.get("environments", {}).items():
        if not env.get("controlled", True):
            continue
        has_device = any(
            d.get("environment") == env_name
            for d in cfg.get("devices", {}).values()
        )
        if not has_device:
            r.warn(f"Environment '{env_name}' has no devices (uncontrollable)")

    # Devices with intended properties should have a sensor
    # that reports those properties in the same environment
    for dev_name, dev in cfg.get("devices", {}).items():
        props = dev.get("intended_properties", {})
        if not props:
            continue
        dev_env = dev.get("environment")
        env_sensors = [
            s for s in cfg.get("sensors", {}).values()
            if s.get("environment") == dev_env
        ]
        for prop in props:
            has_sensor = any(
                prop in s.get("reports", [])
                for s in env_sensors
            )
            if not has_sensor:
                r.warn(
                    f"Device '{dev_name}' targets '{prop}' in '{dev_env}' "
                    f"but no sensor in that environment reports '{prop}' "
                    f"(can control but can't verify)"
                )

    # Devices should have safety config
    for dev_name in cfg.get("devices", {}):
        if dev_name not in cfg.get("safety", {}).get("devices", {}):
            r.warn(f"Device '{dev_name}' has no safety config (no safe_state defined)")


# ── Tier 3: Physics ──────────────────────────────────────────────

def _validate_physics(cfg: dict, r: ValidationResult) -> None:
    """Check physical/logical sanity."""

    devices = cfg.get("devices", {})

    # Energy devices should have intended properties (unless scheduled)
    for name, dev in devices.items():
        if dev.get("type") != "energy":
            continue
        props = dev.get("intended_properties", {})
        if not props and not dev.get("scheduled"):
            r.warn(
                f"Energy device '{name}' has no intended_properties "
                f"and is not scheduled (what does it do?)"
            )

    # Transfer devices should NOT have intended properties
    for name, dev in devices.items():
        if dev.get("type") != "transfer":
            continue
        props = dev.get("intended_properties", {})
        if props:
            r.error(
                f"Transfer device '{name}' should not have intended_properties "
                f"(transfer devices are conductance modifiers — direction "
                f"depends on the differential)"
            )

    # Transfer devices should appear in a connection
    transfer_devices_in_connections = set()
    for conn in cfg.get("connections", {}).values():
        td = conn.get("transfer_device")
        if td:
            transfer_devices_in_connections.add(td)

    for name, dev in devices.items():
        if dev.get("type") == "transfer" and name not in transfer_devices_in_connections:
            r.warn(
                f"Transfer device '{name}' is not referenced by any connection "
                f"(it won't be evaluated — add it to a connection)"
            )

    # Safety limits should be wider than targets
    safety = cfg.get("safety", {})
    default_bands = safety.get("default_bands", {})

    for env_name, sched in cfg.get("schedules", {}).items():
        env_limits = safety.get("environments", {}).get(env_name, {}).get("limits", {})

        for phase in sched.get("phases", []):
            targets = phase.get("targets", {})
            phase_name = phase.get("name", "?")

            for prop, target_val in targets.items():
                limits = env_limits.get(prop, {})
                abs_min = limits.get("absolute_min")
                abs_max = limits.get("absolute_max")
                if abs_min is None or abs_max is None:
                    continue

                # Compute effective band
                if isinstance(target_val, (int, float)):
                    band = default_bands.get(prop, 3.0)
                    effective_min = target_val - band
                    effective_max = target_val + band
                elif isinstance(target_val, dict):
                    effective_min = target_val.get("min", target_val)
                    effective_max = target_val.get("max", target_val)
                else:
                    continue

                if effective_min <= abs_min:
                    r.warn(
                        f"Schedule '{env_name}/{phase_name}': "
                        f"{prop} target band ({effective_min}) at or below "
                        f"safety minimum ({abs_min}) — controller has no room "
                        f"to operate before safety trips"
                    )
                if effective_max >= abs_max:
                    r.warn(
                        f"Schedule '{env_name}/{phase_name}': "
                        f"{prop} target band ({effective_max}) at or above "
                        f"safety maximum ({abs_max}) — controller has no room "
                        f"to operate before safety trips"
                    )

    # Graduated device states should start with 'off'
    for name, dev in devices.items():
        states = dev.get("states")
        if states and states[0] != "off":
            r.warn(
                f"Device '{name}' states {states} — first state should "
                f"be 'off' by convention (controller assumes index 0 = off)"
            )
