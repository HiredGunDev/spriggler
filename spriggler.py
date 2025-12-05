"""Spriggler: Environmental control daemon."""

from loguru import logger
from config_loader import load_config, ConfigError
from controllers.environment_controller import EnvironmentController
from loaders.device_loader import load_devices
from loaders.sensor_loader import load_sensors

import asyncio
import copy
import json
import os
import sys
import time


class Spriggler:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = {}
        self.devices = []
        self.sensors = []
        self._sensor_metadata = {}
        self._device_metadata = {}
        self._sensor_metadata_by_id = {}
        self._sensors_by_id = {}
        self._devices_by_id = {}
        self._last_sensor_data = {}
        self.loop_interval = 1.0
        self.heartbeat_interval = 60.0
        self.environment_controller = None
        self._last_log_time = time.monotonic()

        self.setup_logging()

    def setup_logging(self):
        """Configure Loguru logger based on documented log format."""
        log_file = "logs/spriggler.log"
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        log_format = (
            "{time:YYYY-MM-DDTHH:mm:ss} - "
            "{extra[COMPONENT_TYPE]:<10} - "
            "{extra[ENTITY_NAME]:<15} - "
            "{level:<8} - "
            "{message}"
        )

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
        self._last_log_time = time.monotonic()

    async def initialize_config(self):
        """Initialize the configuration using config_loader."""
        try:
            self.config = load_config(self.config_path)
        except ConfigError as e:
            self.log(
                f"Failed to load configuration: {e}",
                level="ERROR",
                component_type="system",
                entity_name="global",
            )
            raise

        runtime_settings = self.config.get("runtime", {})
        self.loop_interval = float(runtime_settings.get("loop_interval_seconds", self.loop_interval))
        self.heartbeat_interval = max(
            60.0,
            float(runtime_settings.get("heartbeat_interval_seconds", self.heartbeat_interval)),
        )

        self.log(
            "Configuration loaded successfully.",
            level="INFO",
            component_type="system",
            entity_name="global",
        )
        for line in json.dumps(self.config, indent=2, sort_keys=True).splitlines():
            self.log(
                line,
                level="INFO",
                component_type="system",
                entity_name="configuration",
            )

    async def initialize_devices(self):
        """Initialize devices using the device loader."""
        device_definitions = self.config["devices"]["definitions"]
        self.devices = load_devices(device_definitions)

        self._device_metadata.clear()
        self._devices_by_id.clear()

        for device, definition in zip(self.devices, device_definitions):
            device_id = definition["id"]

            try:
                await device.initialize()
            except Exception as exc:
                self.log(
                    f"Failed to initialize device {device_id}: {exc}",
                    level="ERROR",
                    component_type="system",
                    entity_name="global",
                )
                continue

            metadata = device.get_metadata()
            metadata.setdefault("id", device_id)
            metadata.setdefault("what", definition["what"])
            self._device_metadata[device_id] = metadata
            self._devices_by_id[device_id] = device

            self.log(
                f"Device initialized: {json.dumps(metadata, sort_keys=True)}",
                component_type="device",
                entity_name=device_id,
            )

    async def initialize_sensors(self):
        """Initialize sensors using the sensor loader."""
        sensor_definitions = self.config["sensors"]["definitions"]
        self.sensors = load_sensors(sensor_definitions)

        self._sensor_metadata.clear()
        self._sensor_metadata_by_id.clear()
        self._sensors_by_id.clear()

        for sensor, definition in zip(self.sensors, sensor_definitions):
            configured_id = definition["id"]
            hardware_id = getattr(sensor, "id", None)
            configured_id = configured_id or hardware_id or "unknown"

            try:
                await sensor.initialize(logger)
            except TypeError:
                # Sensor doesn't accept logger argument
                await sensor.initialize()

            metadata = sensor.get_metadata()
            metadata = dict(metadata)
            hardware_id = hardware_id or metadata.get("id")
            metadata["id"] = configured_id
            metadata.setdefault("hardware_id", hardware_id)
            metadata.setdefault("what", definition["what"])

            self._sensor_metadata[sensor] = metadata
            self._sensor_metadata_by_id[configured_id] = metadata
            self._sensors_by_id[configured_id] = sensor

            self.log(
                f"Sensor initialized: {json.dumps(metadata, sort_keys=True)}",
                component_type="sensor",
                entity_name=configured_id,
            )

    def initialize_controller(self):
        """Create the environment controller from the loaded configuration."""
        runtime_settings = self.config.get("runtime", {})
        dry_run = bool(runtime_settings.get("dry_run", False))
        debounce_seconds = float(runtime_settings.get("debounce_seconds", 5.0))
        state_refresh_seconds = float(runtime_settings.get("state_refresh_seconds", 60.0))

        self.environment_controller = EnvironmentController(
            config=self.config,
            log_callback=self.log,
            debounce_seconds=debounce_seconds,
            state_refresh_seconds=state_refresh_seconds,
            dry_run=dry_run,
        )

    async def shutdown(self):
        """Cleanup resources before exiting the application."""
        for sensor in self.sensors:
            stop_method = getattr(sensor, "stop_scanning", None)
            if not callable(stop_method):
                continue

            try:
                await stop_method()
            except Exception as exc:
                sensor_id = getattr(sensor, "id", "unknown")
                self.log(
                    f"Sensor shutdown failure: {exc}",
                    level="ERROR",
                    component_type="sensor",
                    entity_name=sensor_id,
                )

    async def run(self, max_cycles=None):
        """Main loop for running the Spriggler system."""
        self.log(
            "Spriggler system starting up.",
            level="INFO",
            component_type="system",
            entity_name="global",
        )

        if self.environment_controller is None:
            self.initialize_controller()

        cycle_count = 0

        try:
            while True:
                sensor_data = await self._poll_sensors()

                if self.environment_controller:
                    await self.environment_controller.evaluate(
                        sensor_data=sensor_data,
                        devices=self._devices_by_id,
                    )

                now = time.monotonic()
                if now - self._last_log_time >= self.heartbeat_interval:
                    self.log(
                        "Heartbeat: Spriggler is running.",
                        component_type="system",
                        entity_name="heartbeat",
                    )

                cycle_count += 1
                if max_cycles is not None and cycle_count >= max_cycles:
                    break

                await asyncio.sleep(self.loop_interval)

        except KeyboardInterrupt:
            self.log(
                "Spriggler system shutting down.",
                level="INFO",
                component_type="system",
                entity_name="global",
            )

    async def _poll_sensors(self):
        """Collect readings from all configured sensors."""
        readings = {}

        for sensor in self.sensors:
            read_method = getattr(sensor, "read", None)
            if not callable(read_method):
                continue

            sensor_metadata = self._sensor_metadata.get(sensor, {})
            sensor_id = sensor_metadata.get("id") or getattr(sensor, "id", "unknown")

            try:
                result = await read_method()
            except Exception as exc:
                self.log(
                    f"Sensor read failure: {exc}",
                    level="ERROR",
                    component_type="sensor",
                    entity_name=sensor_id,
                )
                continue

            if result is None:
                continue

            readings[sensor_id] = result

            previous_result = self._last_sensor_data.get(sensor_id)
            if result != previous_result:
                self.log(
                    f"Sensor data: {result}",
                    component_type="sensor",
                    entity_name=sensor_id,
                )
                self._last_sensor_data[sensor_id] = copy.deepcopy(result)

        return readings


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
        spriggler.initialize_controller()
        await spriggler.run()
    except ConfigError:
        logger.error("Exiting due to configuration error.")
    finally:
        await spriggler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
