from importlib import import_module
from loguru import logger

def load_devices(device_definitions):
    """
    Load and initialize devices based on their definitions in the configuration.

    Args:
        device_definitions (list): A list of device definitions from the configuration file.

    Returns:
        list: A list of initialized device objects.
    """
    devices = []

    for device_config in device_definitions:
        try:
            # Extract the module and class for the device
            model_name = device_config["how"]
            module = import_module(f"devices.{model_name}")

            # Convert model name to PascalCase to match class name
            class_name = "".join(part.capitalize() for part in model_name.split("_"))
            device_class = getattr(module, class_name)

            # Initialize the device with its configuration
            device = device_class(device_config)
            devices.append(device)

            # Log successful initialization
            logger.info(f"Initialized device: {device_config['id']}")
        except Exception as e:
            # Log errors with as much context as possible
            entity_name = device_config.get("id", "unknown")
            logger.error(f"Failed to initialize device {entity_name}: {e}")

    return devices
