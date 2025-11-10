"""Device loader entry points."""

from loaders._entity_loader import load_entities


def load_devices(device_definitions):
    """Load and initialize devices based on their definitions in the configuration."""

    return load_entities(
        device_definitions,
        package="devices",
        kind="device",
    )
