from loguru import logger

from devices.power_state import PowerCommandResult, ensure_power_state


class MockDevice:
    """Simple mock device used for integration testing."""

    def __init__(self, config):
        self.id = config.get("id", "mock_device")
        self.what = config.get("what", "device")
        control = config.get("control", {})
        self.name = control.get("name", self.id)
        self.outlet_name = control.get("outlet_name", "outlet")
        power = config.get("power", {})
        self.power_rating = power.get("rating", 0)
        self.circuit = power.get("circuit", "mock_circuit")
        self.location = config.get("location", self.name)
        self.address = config.get("address", control.get("ip_address", f"mock://{self.name}"))
        self.timeout = config.get("timeout", 300)
        self._is_on = False

    def initialize(self):
        logger.bind(COMPONENT_TYPE="device", ENTITY_NAME=self.id).info(
            "Mock device ready on circuit '{}' using outlet '{}' ({}W).",
            self.circuit,
            self.outlet_name,
            self.power_rating,
        )

    def get_metadata(self):
        return {
            "id": self.id,
            "what": self.what,
            "circuit": self.circuit,
            "outlet": self.outlet_name,
            "power_rating": self.power_rating,
        }

    def is_on(self) -> bool:
        """Return True when the mock device is powered on."""

        return self._is_on

    def turn_on(self) -> PowerCommandResult:
        """Simulate powering on the device."""

        def _command() -> None:
            self._is_on = True

        return ensure_power_state(
            desired_state=True,
            device_id=self.id,
            device_label=self.name,
            read_state=self.is_on,
            command=_command,
        )

    def turn_off(self) -> PowerCommandResult:
        """Simulate powering off the device."""

        def _command() -> None:
            self._is_on = False

        return ensure_power_state(
            desired_state=False,
            device_id=self.id,
            device_label=self.name,
            read_state=self.is_on,
            command=_command,
        )
