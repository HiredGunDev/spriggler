"""Tests for configuration loading and validation.

These tests define the config contract. The validator must pass all of these
before any other code gets written.
"""

import copy
import json
import pytest

from spriggler.config.loader import load_config, ConfigError


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def minimal_config():
    """Smallest valid config: one environment, one sensor, one device, one circuit."""
    return {
        "version": "0.3",
        "name": "Test",
        "units": {
            "temperature": "F"
        },
        "environments": {
            "chamber": {
                "description": "Test chamber"
            }
        },
        "sensors": {
            "temp_sensor": {
                "driver": "govee_h5100",
                "environment": "chamber",
                "properties": ["temperature"],
                "driver_config": {
                    "address": "AA:BB:CC:DD:EE:FF"
                }
            },
            "ambient_sensor": {
                "driver": "govee_h5100",
                "environment": "ambient",
                "properties": ["temperature"],
                "driver_config": {
                    "address": "11:22:33:44:55:66"
                }
            }
        },
        "devices": {
            "heater": {
                "driver": "kasa_plug",
                "environment": "chamber",
                "circuit": "main",
                "role": "heater",
                "driver_config": {
                    "address": "192.168.1.100",
                    "plug_index": 0
                }
            }
        },
        "circuits": {
            "main": {
                "max_amps": 20,
                "voltage": 120
            }
        },
        "schedules": {
            "chamber": {
                "phases": [
                    {
                        "name": "always",
                        "start": "00:00",
                        "end": "00:00",
                        "targets": {
                            "temperature": {"min": 70, "max": 80, "ideal": 75}
                        }
                    }
                ]
            }
        },
        "safety": {
            "environments": {
                "chamber": {
                    "limits": {
                        "temperature": {"absolute_min": 40, "absolute_max": 110}
                    }
                }
            },
            "devices": {
                "heater": {
                    "safe_state": "off"
                }
            },
            "sensor_stale_after_missed": 3,
            "safety_loop_interval_seconds": 15
        }
    }


@pytest.fixture
def full_config():
    """Load the example config as a realistic full config."""
    import os
    config_path = os.path.join(
        os.path.dirname(__file__), '..', 'config', 'example.json'
    )
    with open(config_path) as f:
        return json.load(f)


def mutate(config, path, value=None, delete=False):
    """Helper to mutate a nested config value. Returns a deep copy."""
    cfg = copy.deepcopy(config)
    keys = path.split('.')
    target = cfg
    for key in keys[:-1]:
        target = target[key]
    if delete:
        del target[keys[-1]]
    else:
        target[keys[-1]] = value
    return cfg


# ── Valid configs ────────────────────────────────────────────────────────────

class TestValidConfigs:
    """Configs that should load without error."""

    def test_minimal_config_loads(self, minimal_config):
        """Smallest possible valid config passes validation."""
        load_config(minimal_config)

    def test_full_example_config_loads(self, full_config):
        """The example.json from the repo passes validation."""
        load_config(full_config)

    def test_celsius_units(self, minimal_config):
        """Celsius is a valid temperature unit."""
        cfg = mutate(minimal_config, 'units.temperature', 'C')
        # Adjust targets and limits to Celsius values
        cfg['schedules']['chamber']['phases'][0]['targets']['temperature'] = {
            "min": 21, "max": 27, "ideal": 24
        }
        cfg['safety']['environments']['chamber']['limits']['temperature'] = {
            "absolute_min": 4, "absolute_max": 43
        }
        load_config(cfg)

    def test_environment_without_connections(self, minimal_config):
        """An environment with no connections is valid (isolated chamber)."""
        load_config(minimal_config)  # minimal_config has no connections

    def test_optional_fields_absent(self, minimal_config):
        """Optional fields can be omitted."""
        cfg = copy.deepcopy(minimal_config)
        # Remove optional description
        del cfg['environments']['chamber']['description']
        # Remove optional poll_interval_seconds
        if 'poll_interval_seconds' in cfg['sensors']['temp_sensor']:
            del cfg['sensors']['temp_sensor']['poll_interval_seconds']
        # Remove optional circuit description
        if 'description' in cfg['circuits']['main']:
            del cfg['circuits']['main']['description']
        load_config(cfg)


# ── Missing required top-level sections ──────────────────────────────────────

class TestMissingTopLevel:
    """Missing required top-level sections should be fatal."""

    def test_missing_version(self, minimal_config):
        cfg = mutate(minimal_config, 'version', delete=True)
        with pytest.raises(ConfigError, match="version"):
            load_config(cfg)

    def test_missing_units(self, minimal_config):
        cfg = mutate(minimal_config, 'units', delete=True)
        with pytest.raises(ConfigError, match="units"):
            load_config(cfg)

    def test_missing_environments(self, minimal_config):
        cfg = mutate(minimal_config, 'environments', delete=True)
        with pytest.raises(ConfigError, match="environments"):
            load_config(cfg)

    def test_missing_sensors(self, minimal_config):
        cfg = mutate(minimal_config, 'sensors', delete=True)
        with pytest.raises(ConfigError, match="sensors"):
            load_config(cfg)

    def test_missing_devices(self, minimal_config):
        cfg = mutate(minimal_config, 'devices', delete=True)
        with pytest.raises(ConfigError, match="devices"):
            load_config(cfg)

    def test_missing_circuits(self, minimal_config):
        cfg = mutate(minimal_config, 'circuits', delete=True)
        with pytest.raises(ConfigError, match="circuits"):
            load_config(cfg)

    def test_missing_schedules(self, minimal_config):
        cfg = mutate(minimal_config, 'schedules', delete=True)
        with pytest.raises(ConfigError, match="schedules"):
            load_config(cfg)

    def test_missing_safety(self, minimal_config):
        cfg = mutate(minimal_config, 'safety', delete=True)
        with pytest.raises(ConfigError, match="safety"):
            load_config(cfg)


# ── Units validation ─────────────────────────────────────────────────────────

class TestUnitsValidation:
    """Temperature unit declaration is required and must be valid."""

    def test_missing_temperature_unit(self, minimal_config):
        cfg = mutate(minimal_config, 'units', {})
        with pytest.raises(ConfigError, match="temperature"):
            load_config(cfg)

    def test_invalid_temperature_unit(self, minimal_config):
        cfg = mutate(minimal_config, 'units.temperature', 'K')
        with pytest.raises(ConfigError, match="temperature"):
            load_config(cfg)

    def test_fahrenheit_accepted(self, minimal_config):
        cfg = mutate(minimal_config, 'units.temperature', 'F')
        load_config(cfg)

    def test_celsius_accepted(self, minimal_config):
        cfg = mutate(minimal_config, 'units.temperature', 'C')
        cfg['schedules']['chamber']['phases'][0]['targets']['temperature'] = {
            "min": 21, "max": 27, "ideal": 24
        }
        cfg['safety']['environments']['chamber']['limits']['temperature'] = {
            "absolute_min": 4, "absolute_max": 43
        }
        load_config(cfg)


# ── Sensor validation ────────────────────────────────────────────────────────

class TestSensorValidation:
    """Sensor entries must have required fields and valid references."""

    def test_sensor_missing_driver(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['sensors']['temp_sensor']['driver']
        with pytest.raises(ConfigError, match="driver"):
            load_config(cfg)

    def test_sensor_missing_environment(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['sensors']['temp_sensor']['environment']
        with pytest.raises(ConfigError, match="environment"):
            load_config(cfg)

    def test_sensor_missing_properties(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['sensors']['temp_sensor']['properties']
        with pytest.raises(ConfigError, match="properties"):
            load_config(cfg)

    def test_sensor_missing_driver_config(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['sensors']['temp_sensor']['driver_config']
        with pytest.raises(ConfigError, match="driver_config"):
            load_config(cfg)

    def test_sensor_references_nonexistent_environment(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['sensors']['temp_sensor']['environment'] = 'nonexistent'
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(cfg)

    def test_no_ambient_sensor(self, minimal_config):
        """At least one sensor must be assigned to 'ambient'."""
        cfg = copy.deepcopy(minimal_config)
        del cfg['sensors']['ambient_sensor']
        with pytest.raises(ConfigError, match="ambient"):
            load_config(cfg)


# ── Device validation ────────────────────────────────────────────────────────

class TestDeviceValidation:
    """Device entries must have required fields and valid references."""

    def test_device_missing_driver(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['devices']['heater']['driver']
        with pytest.raises(ConfigError, match="driver"):
            load_config(cfg)

    def test_device_missing_environment(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['devices']['heater']['environment']
        with pytest.raises(ConfigError, match="environment"):
            load_config(cfg)

    def test_device_missing_circuit(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['devices']['heater']['circuit']
        with pytest.raises(ConfigError, match="circuit"):
            load_config(cfg)

    def test_device_missing_role(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['devices']['heater']['role']
        with pytest.raises(ConfigError, match="role"):
            load_config(cfg)

    def test_device_missing_driver_config(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['devices']['heater']['driver_config']
        with pytest.raises(ConfigError, match="driver_config"):
            load_config(cfg)

    def test_device_references_nonexistent_environment(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['devices']['heater']['environment'] = 'nonexistent'
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(cfg)

    def test_device_references_nonexistent_circuit(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['devices']['heater']['circuit'] = 'nonexistent'
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(cfg)

    def test_device_invalid_role(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['devices']['heater']['role'] = 'toaster'
        with pytest.raises(ConfigError, match="role"):
            load_config(cfg)


# ── Circuit validation ───────────────────────────────────────────────────────

class TestCircuitValidation:
    """Circuit entries must have required fields."""

    def test_circuit_missing_max_amps(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['circuits']['main']['max_amps']
        with pytest.raises(ConfigError, match="max_amps"):
            load_config(cfg)

    def test_circuit_missing_voltage(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['circuits']['main']['voltage']
        with pytest.raises(ConfigError, match="voltage"):
            load_config(cfg)


# ── Schedule validation ──────────────────────────────────────────────────────

class TestScheduleValidation:
    """Schedules must cover 24 hours and have valid references."""

    def test_schedule_for_nonexistent_environment(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['schedules']['nonexistent'] = cfg['schedules']['chamber']
        del cfg['schedules']['chamber']
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(cfg)

    def test_environment_without_schedule(self, minimal_config):
        """Every non-ambient environment needs a schedule."""
        cfg = copy.deepcopy(minimal_config)
        del cfg['schedules']['chamber']
        with pytest.raises(ConfigError, match="schedule"):
            load_config(cfg)

    def test_schedule_gap_in_coverage(self, minimal_config):
        """Phases must cover the full 24 hours without gaps."""
        cfg = copy.deepcopy(minimal_config)
        cfg['schedules']['chamber']['phases'] = [
            {
                "name": "partial",
                "start": "06:00",
                "end": "18:00",
                "targets": {
                    "temperature": {"min": 70, "max": 80, "ideal": 75}
                }
            }
        ]
        with pytest.raises(ConfigError, match="24"):
            load_config(cfg)

    def test_phase_missing_targets(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['schedules']['chamber']['phases'][0]['targets']
        with pytest.raises(ConfigError, match="targets"):
            load_config(cfg)

    def test_target_missing_min(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['schedules']['chamber']['phases'][0]['targets']['temperature']['min']
        with pytest.raises(ConfigError, match="min"):
            load_config(cfg)

    def test_target_missing_max(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        del cfg['schedules']['chamber']['phases'][0]['targets']['temperature']['max']
        with pytest.raises(ConfigError, match="max"):
            load_config(cfg)

    def test_target_min_greater_than_max(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['schedules']['chamber']['phases'][0]['targets']['temperature'] = {
            "min": 80, "max": 70
        }
        with pytest.raises(ConfigError, match="min.*max|max.*min"):
            load_config(cfg)

    def test_schedule_device_override_nonexistent_device(self, minimal_config):
        """Device overrides in schedule phases must reference real devices."""
        cfg = copy.deepcopy(minimal_config)
        cfg['schedules']['chamber']['phases'][0]['devices'] = {
            "nonexistent_light": "on"
        }
        with pytest.raises(ConfigError, match="nonexistent_light"):
            load_config(cfg)


# ── Safety validation ────────────────────────────────────────────────────────

class TestSafetyValidation:
    """Safety config must have valid references and sane values."""

    def test_safety_env_references_nonexistent_environment(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['safety']['environments']['nonexistent'] = cfg['safety']['environments']['chamber']
        del cfg['safety']['environments']['chamber']
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(cfg)

    def test_safety_device_references_nonexistent_device(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['safety']['devices']['nonexistent'] = {"safe_state": "off"}
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(cfg)

    def test_safety_limits_narrower_than_targets(self, minimal_config):
        """Absolute limits must be wider than schedule targets."""
        cfg = copy.deepcopy(minimal_config)
        # Target min is 70, set absolute_min to 72 (tighter than target)
        cfg['safety']['environments']['chamber']['limits']['temperature']['absolute_min'] = 72
        with pytest.raises(ConfigError, match="limit.*target|target.*limit"):
            load_config(cfg)

    def test_safety_absolute_max_narrower_than_target_max(self, minimal_config):
        """absolute_max must be greater than target max."""
        cfg = copy.deepcopy(minimal_config)
        # Target max is 80, set absolute_max to 78 (tighter than target)
        cfg['safety']['environments']['chamber']['limits']['temperature']['absolute_max'] = 78
        with pytest.raises(ConfigError, match="limit.*target|target.*limit"):
            load_config(cfg)

    def test_safety_invalid_safe_state(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['safety']['devices']['heater']['safe_state'] = 'maybe'
        with pytest.raises(ConfigError, match="safe_state"):
            load_config(cfg)

    def test_safety_valid_safe_states(self, minimal_config):
        """on, off, and current are all valid safe states."""
        for state in ['on', 'off', 'current']:
            cfg = copy.deepcopy(minimal_config)
            cfg['safety']['devices']['heater']['safe_state'] = state
            load_config(cfg)


# ── Connection validation ────────────────────────────────────────────────────

class TestConnectionValidation:
    """Environment connections must reference valid environments and devices."""

    def test_connection_to_nonexistent_environment(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['environments']['chamber']['connections'] = {
            "nonexistent": {"via": "heater", "bidirectional": False}
        }
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(cfg)

    def test_connection_via_nonexistent_device(self, minimal_config):
        cfg = copy.deepcopy(minimal_config)
        cfg['environments']['chamber']['connections'] = {
            "ambient": {"via": "nonexistent_fan", "bidirectional": False}
        }
        with pytest.raises(ConfigError, match="nonexistent_fan"):
            load_config(cfg)


# ── Environment coverage ─────────────────────────────────────────────────────

class TestEnvironmentCoverage:
    """Every non-ambient environment must have at least one sensor."""

    def test_environment_with_no_sensor(self, minimal_config):
        """An environment with no sensor assigned is invalid."""
        cfg = copy.deepcopy(minimal_config)
        cfg['environments']['orphan'] = {"description": "No sensors here"}
        cfg['schedules']['orphan'] = cfg['schedules']['chamber']
        cfg['safety']['environments']['orphan'] = cfg['safety']['environments']['chamber']
        with pytest.raises(ConfigError, match="sensor"):
            load_config(cfg)
