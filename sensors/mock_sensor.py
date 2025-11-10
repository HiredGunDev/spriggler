from loguru import logger

def get_metadata():
    """Provides metadata for the MockSensor class."""
    metadata = {
        "model": "mock_sensor",
        "description": "Mock Sensor for testing purposes",
        "class": MockSensor
    }
    logger.info(f"Providing metadata for mock_sensor: {metadata}")
    return metadata

class MockSensor:
    def __init__(self, config):
        self.id = config.get("id")
        self.name = config.get("name")
        self.location = config.get("location")
        self.address = config.get("address")
        self.refresh_rate = config.get("refresh_rate", 60)
        self.timeout = config.get("timeout", 300)

    def read(self):
        # Simulate sensor data only (no metadata)
        return {"temperature": 72.0, "humidity": 50.0}

