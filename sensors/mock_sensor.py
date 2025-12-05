"""Mock sensor for integration testing.

Follows the async sensor contract.
"""


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
        self._readings = {"temperature": 72.0, "humidity": 50.0}

    async def initialize(self, spriggler_logger):
        """Initialize the mock sensor."""
        self._logger = spriggler_logger.bind(COMPONENT_TYPE="sensor", ENTITY_NAME=self.id)
        self._logger.info(
            "Mock sensor initialized with identifier '{}' at {}.",
            self.identifier,
            self.address,
        )

    async def read(self):
        """Return mock sensor readings."""
        if self._logger:
            self._logger.debug("Mock sensor reading generated: {}", self._readings)
        return dict(self._readings)

    async def stop_scanning(self):
        """Clean up resources (no-op for mock)."""
        if self._logger:
            self._logger.debug("Mock sensor stopped.")

    def get_metadata(self):
        """Return sensor metadata."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "name": self.name,
            "address": self.address,
            "location": self.location,
            "refresh_rate": self.refresh_rate,
            "timeout": self.timeout,
        }

    def set_readings(self, temperature=None, humidity=None):
        """Set mock readings for testing."""
        if temperature is not None:
            self._readings["temperature"] = temperature
        if humidity is not None:
            self._readings["humidity"] = humidity
