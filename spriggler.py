from loguru import logger
from config_loader import load_config, ConfigError
from loaders.device_loader import load_devices
from loaders.sensor_loader import load_sensors

import os
import sys
import asyncio

class Spriggler:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = {}
        self.devices = []
        self.sensors = []

        # Setup logging
        self.setup_logging()

    def setup_logging(self):
        """Configure Loguru logger based on documented log format."""
        log_file = "logs/spriggler.log"
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        # Log format based on specification
        log_format = (
            "{time:YYYY-MM-DDTHH:mm:ss} - "
            "{extra[COMPONENT_TYPE]:<10} - "
            "{extra[ENTITY_NAME]:<15} - "
            "{level:<8} - "
            "{message}"
        )

        # Configure default values for extra fields
        logger.configure(
            handlers=[
                {
                    "sink": log_file,
                    "format": log_format,
                    "rotation": "10 MB",
                    "retention": "7 days",
                    "compression": "zip",
                    "serialize": False,
                    "level": "INFO",
                },
                {
                    "sink": lambda msg: print(msg, end=""),
                    "format": log_format,
                    "level": "INFO",
                },
            ],
            extra={"COMPONENT_TYPE": "system", "ENTITY_NAME": "global"},
        )

    def log(self, message, level="INFO", component_type="system", entity_name="global"):
        """Centralized logging function with documented fields."""
        if not isinstance(entity_name, str):
            entity_name = str(entity_name)
        logger.bind(COMPONENT_TYPE=component_type, ENTITY_NAME=entity_name).log(level.upper(), message)

    async def initialize_config(self):
        """Initialize the configuration using config_loader."""
        try:
            self.config = load_config(self.config_path)
            self.log("Configuration loaded successfully.", level="INFO", component_type="system", entity_name="global")
        except ConfigError as e:
            self.log(f"Failed to load configuration: {e}", level="ERROR", component_type="system", entity_name="global")
            raise

    async def initialize_devices(self):
        """Initialize devices using the device loader."""
        self.devices = load_devices(self.config.get("devices", {}).get("definitions", []))

    async def initialize_sensors(self):
        """Initialize sensors using the sensor loader."""
        self.sensors = load_sensors(self.config.get("sensors", {}).get("definitions", []))
        for sensor in self.sensors:
            await sensor.initialize(logger)

    async def run(self):
        """Main loop for running the Spriggler system."""
        self.log("Spriggler system starting up.", level="INFO", component_type="system", entity_name="global")
        try:
            while True:
                for sensor in self.sensors:
                    sensor_data = await sensor.read()
                    self.log(
                        f"Sensor data: {sensor_data}",
                        level="INFO",
                        component_type="sensor",
                        entity_name=sensor.get_metadata().get("id", "unknown"),
                    )
                await asyncio.sleep(1)  # Adjust loop delay as needed
        except KeyboardInterrupt:
            self.log("Spriggler system shutting down.", level="INFO", component_type="system", entity_name="global")

# Example usage
async def main():
    if len(sys.argv) < 2:
        print("Usage: python spriggler.py <config_file>")
        sys.exit(1)

    config_path = sys.argv[1]

    spriggler = Spriggler(config_path)
    try:
        await spriggler.initialize_config()
        await spriggler.initialize_devices()
        await spriggler.initialize_sensors()
        await spriggler.run()
    except ConfigError:
        logger.error("Exiting due to configuration error.")

if __name__ == "__main__":
    asyncio.run(main())
