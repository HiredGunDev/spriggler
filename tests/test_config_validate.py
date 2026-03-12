"""Tests for spriggler.config.validate."""

import textwrap
import os
from pathlib import Path
from unittest import mock

import pytest

from spriggler.config.loader import load_config
from spriggler.config.validate import validate_config, ValidationResult


def _make_config(tmp_path, toml_text: str, env_vars: dict | None = None) -> dict:
    """Write TOML to tmp config, load with env vars."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(textwrap.dedent(toml_text))
    env = env_vars or {}
    with mock.patch.dict(os.environ, env, clear=False):
        return load_config(tmp_path)


# ── Valid config ─────────────────────────────────────────────────

VALID_CONFIG = """\
    [meta]
    version = "0.5"
    name = "Test Pod"

    [units]
    temperature = "F"

    [environments.pod]
    description = "Test pod"
    media = ["air"]

    [environments.ambient]
    description = "Outdoor"
    media = ["air"]
    controlled = false

    [connections.pod_to_ambient]
    endpoints = ["pod", "ambient"]
    medium = "air"
    transfer_device = "pod_fan"

    [sensor_defaults]
    fresh_multiplier = 1.5
    aging_multiplier = 3.0
    dead_multiplier = 10.0

    [sensors.pod_sensor]
    driver = "govee_ble"
    environment = "pod"
    medium = "air"
    reports = ["temperature", "humidity"]
    delivery_interval_seconds = 10
    [sensors.pod_sensor.driver_config]
    address = "ABCD"

    [sensors.ambient_sensor]
    driver = "govee_ble"
    environment = "ambient"
    medium = "air"
    reports = ["temperature", "humidity"]
    delivery_interval_seconds = 10
    [sensors.ambient_sensor.driver_config]
    address = "EF01"

    [devices.pod_heater]
    driver = "kasa_plug"
    type = "energy"
    environment = "pod"
    medium = "air"
    circuit = "main"
    [devices.pod_heater.intended_properties]
    temperature = "increase"
    [devices.pod_heater.driver_config]
    plug = "Heater"

    [devices.pod_fan]
    driver = "kasa_plug"
    type = "transfer"
    environment = "pod"
    medium = "air"
    circuit = "main"
    [devices.pod_fan.driver_config]
    plug = "Fan"

    [devices.pod_humidifier]
    driver = "vesync"
    type = "energy"
    environment = "pod"
    medium = "air"
    circuit = "main"
    states = ["off", "low", "high"]
    [devices.pod_humidifier.intended_properties]
    humidity = "increase"
    [devices.pod_humidifier.driver_config]
    name = "Humidifier"

    [circuits.main]
    max_amps = 15
    voltage = 120

    [[schedules.pod.phases]]
    name = "lights_on"
    start = "22:00"
    end = "16:00"
    [schedules.pod.phases.targets]
    temperature = 80
    humidity = 80
    [schedules.pod.phases.devices]
    pod_heater = "on"

    [[schedules.pod.phases]]
    name = "lights_off"
    start = "16:00"
    end = "22:00"
    [schedules.pod.phases.targets]
    temperature = 75
    humidity = 70

    [safety]
    safety_loop_interval_seconds = 15
    [safety.default_bands]
    temperature = 3.0
    humidity = 5.0
    [safety.environments.pod.limits.temperature]
    absolute_min = 50
    absolute_max = 95
    [safety.environments.pod.limits.humidity]
    absolute_min = 20
    absolute_max = 95
    [safety.devices.pod_heater]
    safe_state = "off"
    coherence_window_seconds = 300
    [safety.devices.pod_fan]
    safe_state = "on"
    coherence_window_seconds = 120
    [safety.devices.pod_humidifier]
    safe_state = "off"
    coherence_window_seconds = 300
"""


def test_valid_config_passes(tmp_path):
    """A well-formed config produces no errors."""
    cfg = _make_config(tmp_path, VALID_CONFIG)
    result = validate_config(cfg)
    assert result.ok, f"Unexpected errors: {result.errors}"


# ── Schema checks ────────────────────────────────────────────────

def test_missing_meta(tmp_path):
    cfg = _make_config(tmp_path, """\
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
    """)
    result = validate_config(cfg)
    assert any("meta" in e for e in result.errors)


def test_missing_environments(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
    """)
    result = validate_config(cfg)
    assert any("environments" in e for e in result.errors)


def test_missing_sensors(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
    """)
    result = validate_config(cfg)
    assert any("sensors" in e for e in result.errors)


def test_sensor_missing_required_fields(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.bad]
        driver = "govee_ble"
    """)
    result = validate_config(cfg)
    assert any("environment" in e for e in result.errors)
    assert any("reports" in e for e in result.errors)
    assert any("delivery_interval" in e for e in result.errors)


def test_invalid_device_type(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [devices.bad]
        driver = "kasa"
        type = "magic"
        environment = "pod"
    """)
    result = validate_config(cfg)
    assert any("magic" in e and "invalid" in e for e in result.errors)


def test_invalid_property_direction(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [devices.bad]
        driver = "kasa"
        type = "energy"
        environment = "pod"
        [devices.bad.intended_properties]
        temperature = "sideways"
    """)
    result = validate_config(cfg)
    assert any("sideways" in e for e in result.errors)


def test_states_must_include_off(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [devices.bad]
        driver = "vesync"
        type = "energy"
        environment = "pod"
        states = ["low", "high"]
    """)
    result = validate_config(cfg)
    assert any("off" in e for e in result.errors)


# ── Semantic checks ──────────────────────────────────────────────

def test_device_references_unknown_environment(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [devices.heater]
        driver = "kasa"
        type = "energy"
        environment = "nonexistent"
    """)
    result = validate_config(cfg)
    assert any("nonexistent" in e for e in result.errors)


def test_device_references_unknown_circuit(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [devices.heater]
        driver = "kasa"
        type = "energy"
        environment = "pod"
        circuit = "ghost_circuit"
    """)
    result = validate_config(cfg)
    assert any("ghost_circuit" in e for e in result.errors)


def test_connection_references_unknown_endpoint(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [connections.bad]
        endpoints = ["pod", "narnia"]
        medium = "air"
    """)
    result = validate_config(cfg)
    assert any("narnia" in e for e in result.errors)


def test_connection_references_unknown_transfer_device(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [environments.ambient]
        media = ["air"]
        controlled = false
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [connections.c1]
        endpoints = ["pod", "ambient"]
        medium = "air"
        transfer_device = "ghost_fan"
    """)
    result = validate_config(cfg)
    assert any("ghost_fan" in e for e in result.errors)


def test_warns_device_without_sensor_coverage(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["humidity"]
        delivery_interval_seconds = 10
        [devices.heater]
        driver = "kasa"
        type = "energy"
        environment = "pod"
        [devices.heater.intended_properties]
        temperature = "increase"
    """)
    result = validate_config(cfg)
    # Heater targets temperature but sensor only reports humidity
    assert any("can control but can't verify" in w for w in result.warnings)


def test_warns_device_without_safety_config(tmp_path):
    cfg = _make_config(tmp_path, VALID_CONFIG)
    # Remove one device's safety config by loading and modifying
    del cfg["safety"]["devices"]["pod_heater"]
    result = validate_config(cfg)
    assert any("pod_heater" in w and "no safety config" in w for w in result.warnings)


# ── Physics checks ───────────────────────────────────────────────

def test_transfer_device_with_intended_properties_is_error(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [devices.bad_fan]
        driver = "kasa"
        type = "transfer"
        environment = "pod"
        [devices.bad_fan.intended_properties]
        temperature = "decrease"
    """)
    result = validate_config(cfg)
    assert any("transfer" in e.lower() and "intended_properties" in e for e in result.errors)


def test_transfer_device_not_in_connection_warns(tmp_path):
    cfg = _make_config(tmp_path, """\
        [meta]
        version = "0.5"
        [environments.pod]
        media = ["air"]
        [sensors.s1]
        driver = "govee_ble"
        environment = "pod"
        reports = ["temperature"]
        delivery_interval_seconds = 10
        [devices.lonely_fan]
        driver = "kasa"
        type = "transfer"
        environment = "pod"
    """)
    result = validate_config(cfg)
    assert any("lonely_fan" in w and "not referenced" in w for w in result.warnings)


def test_valid_config_no_warnings_strict(tmp_path):
    """The full valid config should also pass in strict mode."""
    cfg = _make_config(tmp_path, VALID_CONFIG)
    result = validate_config(cfg)
    assert result.ok, f"Errors: {result.errors}"
    assert result.ok_strict(), f"Warnings: {result.warnings}"
