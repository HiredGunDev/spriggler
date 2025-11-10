from loguru import logger


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
