import json
import os

class ConfigError(Exception):
    """Custom exception for configuration-related errors."""
    pass

def load_config(config_file_path):
    """
    Loads and validates a configuration file.

    Args:
        config_file_path (str): Path to the configuration file.

    Returns:
        dict: The loaded configuration data.

    Raises:
        ConfigError: If the file is missing, invalid, or fails validation.
    """
    if not os.path.exists(config_file_path):
        raise ConfigError(f"Configuration file not found: {config_file_path}")

    try:
        with open(config_file_path, 'r') as file:
            config = json.load(file)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in configuration file: {e}")

    # Validation logic for seedling.json
    if 'header' not in config or 'name' not in config['header']:
        raise ConfigError("Missing 'header' or 'header.name' in configuration.")

    if 'environments' not in config or 'definitions' not in config['environments']:
        raise ConfigError("Missing 'environments.definitions' in configuration.")

    if 'devices' not in config or 'definitions' not in config['devices']:
        raise ConfigError("Missing 'devices.definitions' in configuration.")

    # Additional validation can be added here if necessary

    return config
