import asyncio
import pytest
import types
from loaders.sensor_loader import load_sensors


# Mock sensor class for testing (sync version for loader tests)
class MockSensor:
    def __init__(self, config):
        self.id = config.get("id")
        self.name = config.get("name")
        self.location = config.get("location")
        self.address = config.get("address")
        self.refresh_rate = config.get("refresh_rate", 60)
        self.timeout = config.get("timeout", 300)

    async def read(self):
        return {"temperature": 72.0, "humidity": 50.0}


def test_load_valid_sensor(monkeypatch):
    """Test successful loading of a valid sensor."""
    # Mock the import_module function
    def mock_import_module(module_name):
        if module_name == "sensors.mock_sensor":
            mock_module = types.ModuleType("sensors.mock_sensor")
            mock_module.MockSensor = MockSensor
            return mock_module
        raise ModuleNotFoundError(f"No module named '{module_name}'")

    monkeypatch.setattr("importlib.import_module", mock_import_module)

    # Sensor definition
    sensor_definitions = [
        {
            "id": "test_sensor",
            "how": "mock_sensor",
            "name": "Test Sensor",
            "location": "test_location",
            "address": "mock://address",
            "refresh_rate": 60,
            "timeout": 300
        }
    ]

    sensors = load_sensors(sensor_definitions)

    # Validate loaded sensors
    assert len(sensors) == 1
    sensor = sensors[0]
    assert sensor.id == "test_sensor"
    assert sensor.name == "Test Sensor"
    assert sensor.location == "test_location"
    assert sensor.address == "mock://address"
    assert sensor.refresh_rate == 60
    assert sensor.timeout == 300
    assert asyncio.run(sensor.read()) == {"temperature": 72.0, "humidity": 50.0}

def test_missing_sensor_module(monkeypatch):
    """Test behavior when a sensor module is missing."""
    def mock_import_module(module_name):
        raise ModuleNotFoundError(f"No module named '{module_name}'")

    monkeypatch.setattr("importlib.import_module", mock_import_module)

    # Sensor definition
    sensor_definitions = [
        {
            "id": "missing_sensor",
            "how": "nonexistent_sensor",
            "config": {}
        }
    ]

    sensors = load_sensors(sensor_definitions)

    assert len(sensors) == 0

def test_invalid_sensor_class(monkeypatch):
    """Test behavior when a sensor class is improperly implemented."""
    def mock_import_module(module_name):
        class InvalidSensor:
            pass
        mock_module = types.ModuleType("sensors.invalid_sensor")
        mock_module.InvalidSensor = InvalidSensor
        return mock_module

    monkeypatch.setattr("importlib.import_module", mock_import_module)

    # Sensor definition
    sensor_definitions = [
        {
            "id": "invalid_sensor",
            "how": "invalid_sensor",
            "config": {}
        }
    ]

    sensors = load_sensors(sensor_definitions)

    assert len(sensors) == 0
