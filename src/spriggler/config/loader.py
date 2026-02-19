"""Configuration loading and validation.

Loads a Spriggler config from JSON and validates all references,
required fields, and cross-section consistency.
"""

import json
from pathlib import Path


class ConfigError(Exception):
    """Raised when configuration is invalid."""
    pass


def load_config(config: dict | str | Path) -> dict:
    """Load and validate a Spriggler configuration.

    Args:
        config: Either a dict (already parsed) or a path to a JSON file.

    Returns:
        The validated configuration dict.

    Raises:
        ConfigError: If the configuration is invalid.
    """
    if isinstance(config, (str, Path)):
        path = Path(config)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        with open(path) as f:
            config = json.load(f)

    _validate(config)
    return config


# ── Valid values ─────────────────────────────────────────────────────────────

VALID_TEMP_UNITS = {'F', 'C'}
VALID_ROLES = {
    'heater', 'exhaust', 'intake', 'transfer',
    'humidifier', 'dehumidifier', 'light', 'circulation'
}
VALID_SAFE_STATES = {'on', 'off', 'current'}

REQUIRED_TOP_LEVEL = [
    'version', 'units', 'environments', 'sensors',
    'devices', 'circuits', 'schedules', 'safety'
]

REQUIRED_SENSOR_FIELDS = ['driver', 'environment', 'properties', 'driver_config']
REQUIRED_DEVICE_FIELDS = ['driver', 'environment', 'circuit', 'role', 'driver_config']
REQUIRED_CIRCUIT_FIELDS = ['max_amps', 'voltage']


# ── Validation ───────────────────────────────────────────────────────────────

def _validate(config: dict) -> None:
    """Run all validation checks. Raises ConfigError on first failure."""

    _validate_top_level(config)
    _validate_units(config)
    _validate_sensors(config)
    _validate_devices(config)
    _validate_circuits(config)
    _validate_connections(config)
    _validate_schedules(config)
    _validate_safety(config)
    _validate_environment_coverage(config)


def _validate_top_level(config: dict) -> None:
    for key in REQUIRED_TOP_LEVEL:
        if key not in config:
            raise ConfigError(f"Missing required top-level key: '{key}'")


def _validate_units(config: dict) -> None:
    units = config['units']
    if 'temperature' not in units:
        raise ConfigError("Missing required unit: 'units.temperature'")
    if units['temperature'] not in VALID_TEMP_UNITS:
        raise ConfigError(
            f"Invalid temperature unit: '{units['temperature']}'. "
            f"Must be one of: {', '.join(sorted(VALID_TEMP_UNITS))}"
        )


def _valid_environments(config: dict) -> set:
    """All valid environment names including 'ambient'."""
    return set(config['environments'].keys()) | {'ambient'}


def _validate_sensors(config: dict) -> None:
    valid_envs = _valid_environments(config)

    for sensor_id, sensor in config['sensors'].items():
        for field in REQUIRED_SENSOR_FIELDS:
            if field not in sensor:
                raise ConfigError(
                    f"Sensor '{sensor_id}' missing required field: '{field}'"
                )

        env = sensor['environment']
        if env not in valid_envs:
            raise ConfigError(
                f"Sensor '{sensor_id}' references nonexistent "
                f"environment: '{env}'"
            )

    # At least one ambient sensor required
    ambient_sensors = [
        sid for sid, s in config['sensors'].items()
        if s.get('environment') == 'ambient'
    ]
    if not ambient_sensors:
        raise ConfigError(
            "No ambient sensor configured. At least one sensor must have "
            "environment: 'ambient'. The physics model requires ambient data."
        )


def _validate_devices(config: dict) -> None:
    valid_envs = _valid_environments(config)
    valid_circuits = set(config['circuits'].keys())

    for device_id, device in config['devices'].items():
        for field in REQUIRED_DEVICE_FIELDS:
            if field not in device:
                raise ConfigError(
                    f"Device '{device_id}' missing required field: '{field}'"
                )

        env = device['environment']
        if env not in valid_envs:
            raise ConfigError(
                f"Device '{device_id}' references nonexistent "
                f"environment: '{env}'"
            )

        circuit = device['circuit']
        if circuit not in valid_circuits:
            raise ConfigError(
                f"Device '{device_id}' references nonexistent "
                f"circuit: '{circuit}'"
            )

        role = device['role']
        if role not in VALID_ROLES:
            raise ConfigError(
                f"Device '{device_id}' has invalid role: '{role}'. "
                f"Must be one of: {', '.join(sorted(VALID_ROLES))}"
            )


def _validate_circuits(config: dict) -> None:
    for circuit_id, circuit in config['circuits'].items():
        for field in REQUIRED_CIRCUIT_FIELDS:
            if field not in circuit:
                raise ConfigError(
                    f"Circuit '{circuit_id}' missing required field: '{field}'"
                )


def _validate_connections(config: dict) -> None:
    valid_envs = _valid_environments(config)
    valid_devices = set(config['devices'].keys())

    for env_id, env in config['environments'].items():
        connections = env.get('connections', {})
        for target_env, conn in connections.items():
            if target_env not in valid_envs:
                raise ConfigError(
                    f"Environment '{env_id}' has connection to nonexistent "
                    f"environment: '{target_env}'"
                )
            via_device = conn.get('via')
            if via_device and via_device not in valid_devices:
                raise ConfigError(
                    f"Environment '{env_id}' connection to '{target_env}' "
                    f"references nonexistent device: '{via_device}'"
                )


def _validate_schedules(config: dict) -> None:
    real_envs = set(config['environments'].keys())
    valid_devices = set(config['devices'].keys())

    # Every schedule must reference a real environment
    for sched_env in config['schedules']:
        if sched_env not in real_envs:
            raise ConfigError(
                f"Schedule defined for nonexistent environment: '{sched_env}'"
            )

    # Every real environment must have a schedule
    for env_id in real_envs:
        if env_id not in config['schedules']:
            raise ConfigError(
                f"Environment '{env_id}' has no schedule defined"
            )

    # Validate each schedule
    for sched_env, schedule in config['schedules'].items():
        phases = schedule.get('phases', [])
        if not phases:
            raise ConfigError(
                f"Schedule for '{sched_env}' has no phases"
            )

        for i, phase in enumerate(phases):
            phase_name = phase.get('name', f'phase_{i}')

            # Targets required
            if 'targets' not in phase:
                raise ConfigError(
                    f"Phase '{phase_name}' in schedule '{sched_env}' "
                    f"missing 'targets'"
                )

            # Validate targets
            for prop, target in phase['targets'].items():
                if 'min' not in target:
                    raise ConfigError(
                        f"Target '{prop}' in phase '{phase_name}' "
                        f"(schedule '{sched_env}') missing 'min'"
                    )
                if 'max' not in target:
                    raise ConfigError(
                        f"Target '{prop}' in phase '{phase_name}' "
                        f"(schedule '{sched_env}') missing 'max'"
                    )
                if target['min'] >= target['max']:
                    raise ConfigError(
                        f"Target '{prop}' in phase '{phase_name}' "
                        f"(schedule '{sched_env}'): min ({target['min']}) "
                        f"must be less than max ({target['max']})"
                    )

            # Validate device overrides reference real devices
            for dev_id in phase.get('devices', {}):
                if dev_id not in valid_devices:
                    raise ConfigError(
                        f"Phase '{phase_name}' in schedule '{sched_env}' "
                        f"references nonexistent device: '{dev_id}'"
                    )

        # Validate 24-hour coverage
        _validate_phase_coverage(sched_env, phases)


def _time_to_minutes(time_str: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    h, m = time_str.split(':')
    return int(h) * 60 + int(m)


def _validate_phase_coverage(env_id: str, phases: list) -> None:
    """Verify that phases cover the full 24 hours."""
    # Build a set of all covered minutes
    covered = set()

    for phase in phases:
        start = _time_to_minutes(phase['start'])
        end = _time_to_minutes(phase['end'])

        if start == end:
            # 00:00 to 00:00 means full 24 hours
            covered = set(range(1440))
            break
        elif end > start:
            # Normal: 06:00 to 18:00
            covered.update(range(start, end))
        else:
            # Wraps midnight: 18:00 to 06:00
            covered.update(range(start, 1440))
            covered.update(range(0, end))

    if len(covered) < 1440:
        uncovered = 1440 - len(covered)
        raise ConfigError(
            f"Schedule for '{env_id}' does not cover 24 hours. "
            f"{uncovered} minutes uncovered."
        )


def _validate_safety(config: dict) -> None:
    real_envs = set(config['environments'].keys())
    valid_devices = set(config['devices'].keys())

    safety = config['safety']

    # Validate environment safety references
    for env_id in safety.get('environments', {}):
        if env_id not in real_envs:
            raise ConfigError(
                f"Safety config references nonexistent "
                f"environment: '{env_id}'"
            )

    # Validate device safety references
    for dev_id, dev_safety in safety.get('devices', {}).items():
        if dev_id not in valid_devices:
            raise ConfigError(
                f"Safety config references nonexistent device: '{dev_id}'"
            )
        safe_state = dev_safety.get('safe_state')
        if safe_state and safe_state not in VALID_SAFE_STATES:
            raise ConfigError(
                f"Device '{dev_id}' has invalid safe_state: '{safe_state}'. "
                f"Must be one of: {', '.join(sorted(VALID_SAFE_STATES))}"
            )

    # Validate safety limits vs schedule targets
    _validate_limits_vs_targets(config)


def _validate_limits_vs_targets(config: dict) -> None:
    """Safety absolute limits must be wider than schedule target ranges."""
    safety_envs = config['safety'].get('environments', {})
    schedules = config['schedules']

    for env_id, env_safety in safety_envs.items():
        if env_id not in schedules:
            continue

        limits = env_safety.get('limits', {})
        phases = schedules[env_id].get('phases', [])

        for prop, limit in limits.items():
            abs_min = limit.get('absolute_min')
            abs_max = limit.get('absolute_max')

            # Find the tightest target range across all phases
            for phase in phases:
                target = phase.get('targets', {}).get(prop)
                if not target:
                    continue

                target_min = target.get('min')
                target_max = target.get('max')

                if abs_min is not None and target_min is not None:
                    if abs_min >= target_min:
                        raise ConfigError(
                            f"Safety limit for '{prop}' in '{env_id}': "
                            f"absolute_min ({abs_min}) must be less than "
                            f"target min ({target_min})"
                        )

                if abs_max is not None and target_max is not None:
                    if abs_max <= target_max:
                        raise ConfigError(
                            f"Safety limit for '{prop}' in '{env_id}': "
                            f"absolute_max ({abs_max}) must be greater than "
                            f"target max ({target_max})"
                        )


def _validate_environment_coverage(config: dict) -> None:
    """Every non-ambient environment must have at least one sensor."""
    real_envs = set(config['environments'].keys())
    sensor_envs = {s['environment'] for s in config['sensors'].values()}

    for env_id in real_envs:
        if env_id not in sensor_envs:
            raise ConfigError(
                f"Environment '{env_id}' has no sensor assigned. "
                f"Every environment must have at least one sensor."
            )
