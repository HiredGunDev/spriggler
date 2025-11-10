import pytest
import types
from loaders.device_loader import load_devices

# Mock device class for testing
class MockDevice:
    def __init__(self, config):
        self.id = config.get("id")
        self.location = config.get("location")
        self.address = config.get("address")
        self.timeout = config.get("timeout", 300)

def test_load_valid_device(monkeypatch):
    """Test successful loading of a valid device."""
    # Mock the import_module function
    def mock_import_module(module_name):
        if module_name == "devices.mock_device":
            mock_module = types.ModuleType("devices.mock_device")
            mock_module.MockDevice = MockDevice
            return mock_module
        raise ModuleNotFoundError(f"No module named '{module_name}'")

    monkeypatch.setattr("importlib.import_module", mock_import_module)

    # Device definition
    device_definitions = [
        {
            "id": "test_device",
            "how": "mock_device",
            "location": "test_location",
            "address": "mock://device_address",
            "timeout": 300
        }
    ]

    devices = load_devices(device_definitions)

    # Validate loaded devices
    assert len(devices) == 1
    device = devices[0]
    assert device.id == "test_device"
    assert device.location == "test_location"
    assert device.address == "mock://device_address"
    assert device.timeout == 300

def test_missing_device_module(monkeypatch):
    """Test behavior when a device module is missing."""
    def mock_import_module(module_name):
        raise ModuleNotFoundError(f"No module named '{module_name}'")

    monkeypatch.setattr("importlib.import_module", mock_import_module)

    # Device definition
    device_definitions = [
        {
            "id": "missing_device",
            "how": "nonexistent_device",
            "location": "test_location",
            "address": "mock://missing_device_address"
        }
    ]

    devices = load_devices(device_definitions)

    assert len(devices) == 0

def test_invalid_device_class(monkeypatch):
    """Test behavior when a device class is improperly implemented."""
    def mock_import_module(module_name):
        class InvalidDevice:
            pass
        mock_module = types.ModuleType("devices.invalid_device")
        mock_module.InvalidDevice = InvalidDevice
        return mock_module

    monkeypatch.setattr("importlib.import_module", mock_import_module)

    # Device definition
    device_definitions = [
        {
            "id": "invalid_device",
            "how": "invalid_device",
            "location": "test_location",
            "address": "mock://invalid_device_address"
        }
    ]

    devices = load_devices(device_definitions)

    assert len(devices) == 0
