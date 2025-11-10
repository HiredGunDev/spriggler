import pytest
from config_loader import load_config, ConfigError

def test_load_valid_config():
    """Test loading a valid configuration file."""
    config = load_config('config/seedling.json')
    assert isinstance(config, dict)
    assert 'header' in config  # Example: Ensure a key 'header' exists


def test_missing_config_file():
    """Test behavior when the configuration file is missing."""
    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config('config/nonexistent.json')


def test_invalid_config_file():
    """Test behavior when the configuration file contains invalid JSON."""
    with pytest.raises(ConfigError, match="Invalid JSON in configuration file"):
        load_config('config/invalid.json')


def test_partial_config():
    """Test behavior when the configuration file is missing required keys."""
    # Assuming `load_config` validates required keys
    with pytest.raises(ConfigError, match="Missing"):
        load_config('config/partial.json')
