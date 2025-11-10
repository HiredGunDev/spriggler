from loguru import logger


class MockSensor:
    """Simple mock sensor that produces deterministic readings."""

    def __init__(self, config):
        nested = config.get("config", {})

        self.id = config.get("id") or nested.get("identifier", "mock_sensor")
        self.identifier = nested.get("identifier", self.id)
        self.name = config.get("name", nested.get("name", self.id))
        self.address = config.get("address", nested.get("address", f"mock://{self.identifier}"))
        self.location = config.get("location", nested.get("location", "test_location"))
        self.refresh_rate = nested.get("refresh_rate", config.get("refresh_rate", 60))
        self.timeout = nested.get("timeout", config.get("timeout", 300))
        self._logger = None

    async def initialize(self, spriggler_logger):
        self._logger = spriggler_logger.bind(COMPONENT_TYPE="sensor", ENTITY_NAME=self.id)
        self._logger.info(
            "Mock sensor initialized with identifier '{}' at {}.",
            self.identifier,
            self.address,
        )

    def read(self):
        reading = {"temperature": 72.0, "humidity": 50.0}
        if self._logger:
            self._logger.debug("Mock sensor reading generated: {}", reading)
        return reading

    def get_metadata(self):
        return {
            "id": self.id,
            "identifier": self.identifier,
            "name": self.name,
            "address": self.address,
            "location": self.location,
            "refresh_rate": self.refresh_rate,
            "timeout": self.timeout,
        }

