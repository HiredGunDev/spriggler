from loguru import logger

def get_metadata():
    return {
        "model": "mock_device",
        "description": "Mock Device for testing purposes",
        "class": MockDevice,
    }

class MockDevice:
    def __init__(self, config):
        self.id = config.get("id", "mock_device")
        self.location = config.get("location", "test_location")
        self.address = config.get("address", "mock://device_address")
        self.timeout = config.get("timeout", 300)

        # Log the creation of the device
        logger.info(
            f"MockDevice created with ID: {self.id}, location: {self.location}, address: {self.address}, timeout: {self.timeout}",
            extra={"log_type": "control", "context": self.id}
        )

    def initialize(self):
        logger.info(
            f"Initializing MockDevice {self.id} at {self.address} in {self.location}",
            extra={"log_type": "control", "context": self.id}
        )
