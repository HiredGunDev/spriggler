import asyncio
import time

from controllers.environment_controller import EnvironmentController
from devices.power_state import ensure_power_state


class TrackingDevice:
    def __init__(self, *, initial_state: bool):
        self.id = "tracking_device"
        self.is_on_state = initial_state
        self.turn_on_called = False
        self.turn_off_called = False

    async def is_on(self) -> bool:
        return self.is_on_state

    async def turn_on(self):
        async def _command():
            self.turn_on_called = True
            self.is_on_state = True

        return await ensure_power_state(
            desired_state=True,
            device_id=self.id,
            device_label=self.id,
            read_state=self.is_on,
            command=_command,
        )

    async def turn_off(self):
        async def _command():
            self.turn_off_called = True
            self.is_on_state = False

        return await ensure_power_state(
            desired_state=False,
            device_id=self.id,
            device_label=self.id,
            read_state=self.is_on,
            command=_command,
        )


def build_controller():
    config = {
        "environments": {
            "definitions": [
                {
                    "id": "env1",
                    "properties": {
                        "power": {
                            "controllers": ["dev1"],
                            "schedules": ["always"],
                        }
                    },
                }
            ]
        },
        "schedules": {
            "definitions": [
                {"id": "always", "time_range": None, "targets": {"power": "on"}}
            ]
        },
        "devices": {
            "definitions": [
                {
                    "id": "dev1",
                    "what": "mock",
                    "effects": [
                        {
                            "property": "power",
                            "policy": {
                                "increase": "on",
                                "stable": "off",
                                "decrease": "off",
                            },
                        }
                    ],
                }
            ],
            "defaults": {"effects": {}},
        },
    }

    return EnvironmentController(config=config, debounce_seconds=0, state_refresh_seconds=0)


def build_logging_controller(*, state_refresh_seconds: float = 1.0):
    logs = []

    def log_callback(message, *, level, component_type, entity_name):
        logs.append({
            "message": message,
            "level": level,
            "component_type": component_type,
            "entity_name": entity_name,
        })

    config = {
        "environments": {
            "definitions": [
                {
                    "id": "env1",
                    "properties": {
                        "temperature": {
                            "controllers": [],
                            "schedules": ["always"],
                            "sensors": ["sensor1"],
                        }
                    },
                }
            ]
        },
        "schedules": {
            "definitions": [
                {
                    "id": "always",
                    "time_range": None,
                    "targets": {"temperature": {"min": 20, "max": 25}},
                }
            ]
        },
        "devices": {"definitions": [], "defaults": {"effects": {}}},
    }

    controller = EnvironmentController(
        config=config,
        debounce_seconds=0,
        state_refresh_seconds=state_refresh_seconds,
        log_callback=log_callback,
    )

    return controller, logs


def test_skips_command_when_device_already_on():
    controller = build_controller()
    device = TrackingDevice(initial_state=True)

    asyncio.run(
        controller._apply_state_targets(
            environment_id="env1",
            property_name="power",
            desired_state="on",
            controllers=["dev1"],
            devices={"dev1": device},
        )
    )

    assert device.turn_on_called is False


def test_issues_command_when_state_differs():
    controller = build_controller()
    device = TrackingDevice(initial_state=False)

    asyncio.run(
        controller._apply_state_targets(
            environment_id="env1",
            property_name="power",
            desired_state="on",
            controllers=["dev1"],
            devices={"dev1": device},
        )
    )

    assert device.turn_on_called is True


def test_missing_readings_suppressed_until_cooldown():
    controller, logs = build_logging_controller(state_refresh_seconds=0.5)

    asyncio.run(controller.evaluate(sensor_data={}, devices={}))
    asyncio.run(controller.evaluate(sensor_data={}, devices={}))

    missing_logs = [log for log in logs if "No readings available" in log["message"]]
    assert len(missing_logs) == 1

    time.sleep(0.6)
    asyncio.run(controller.evaluate(sensor_data={}, devices={}))

    missing_logs = [log for log in logs if "No readings available" in log["message"]]
    assert len(missing_logs) == 2


def test_missing_readings_reset_after_data():
    controller, logs = build_logging_controller(state_refresh_seconds=10)

    asyncio.run(controller.evaluate(sensor_data={}, devices={}))
    asyncio.run(
        controller.evaluate(
            sensor_data={"sensor1": {"temperature": 22}},
            devices={},
        )
    )
    asyncio.run(controller.evaluate(sensor_data={}, devices={}))

    missing_logs = [log for log in logs if "No readings available" in log["message"]]
    assert len(missing_logs) == 2
