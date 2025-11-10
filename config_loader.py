import json
import os
from pathlib import Path

from schema_validator import SchemaValidationError, validate_schema

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
        with open(config_file_path, 'r', encoding='utf-8') as file:
            config = json.load(file)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in configuration file: {e}")

    schema_path = Path(__file__).with_name('docs').joinpath('configuration_schema.json')
    try:
        schema_text = schema_path.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration schema not found: {schema_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to read configuration schema: {schema_path}") from exc

    try:
        schema = json.loads(schema_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in configuration schema: {exc}") from exc

    try:
        validate_schema(config, schema)
    except SchemaValidationError as exc:
        raise ConfigError(f"Schema validation error at {exc.location}: {exc.message}") from exc

    return config
