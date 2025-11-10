"""Sensor loader entry points."""

from loaders._entity_loader import load_entities


def _merge_nested_config(sensor_config):
    merged_config = dict(sensor_config)
    nested_config = sensor_config.get("config", {})
    if isinstance(nested_config, dict):
        merged_config = {**nested_config, **merged_config}
    return merged_config


def load_sensors(sensor_definitions):
    """Load and initialize sensors based on their definitions in the configuration."""

    return load_entities(
        sensor_definitions,
        package="sensors",
        kind="sensor",
        config_adapter=_merge_nested_config,
    )
