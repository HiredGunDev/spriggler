import asyncio

from controllers.environment_controller import EnvironmentController


class TrackingDevice:
    def __init__(self, *, initial_state: bool):
        self.is_on_state = initial_state
        self.turn_on_called = False
        self.turn_off_called = False

    def is_on(self) -> bool:
        return self.is_on_state

    def turn_on(self):
        self.turn_on_called = True
        self.is_on_state = True

    def turn_off(self):
        self.turn_off_called = True
        self.is_on_state = False


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
                    "effects": [{"property": "power", "type": "increase"}],
                }
            ],
            "defaults": {"effects": {}},
        },
    }

    return EnvironmentController(config=config, debounce_seconds=0, state_refresh_seconds=0)


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
