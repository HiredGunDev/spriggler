from importlib import import_module
from loguru import logger

def load_sensors(sensor_definitions):
    """
    Load and initialize sensors based on their definitions in the configuration.

    Args:
        sensor_definitions (list): A list of sensor definitions from the configuration file.

    Returns:
        list: A list of initialized sensor objects.
    """
    sensors = []

    for sensor_config in sensor_definitions:
        try:
            # Extract the module and class for the sensor
            model_name = sensor_config["how"]
            module = import_module(f"sensors.{model_name}")

            # Convert model name to PascalCase to match class name
            class_name = "".join(part.capitalize() for part in model_name.split("_"))
            sensor_class = getattr(module, class_name)

            # Initialize the sensor with its configuration
            sensor = sensor_class(sensor_config)
            sensors.append(sensor)

            # Log successful initialization
            logger.info(f"Initialized sensor: {sensor_config['id']}")
        except Exception as e:
            # Log errors with as much context as possible
            entity_name = sensor_config.get("id", "unknown")
            logger.error(f"Failed to initialize sensor {entity_name}: {e}")

    return sensors
