"""Tests for spriggler.config.loader."""

import os
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from spriggler.config.loader import ConfigError, load_config


@pytest.fixture
def spriggler_home(tmp_path):
    """Create a minimal SPRIGGLER_HOME with config.toml."""
    config = tmp_path / "config.toml"
    config.write_text(textwrap.dedent("""\
        [meta]
        version = "0.5"
        name = "Test Pod"

        [units]
        temperature = "F"

        [devices.humidifier.driver_config]
        name = "Dual 200S"
        email = "$SPRIGGLER_VESYNC_EMAIL"
        password = "$SPRIGGLER_VESYNC_PASSWORD"
    """))
    return tmp_path


def test_load_basic(spriggler_home):
    """Config loads and resolves env vars."""
    env = {
        "SPRIGGLER_VESYNC_EMAIL": "test@example.com",
        "SPRIGGLER_VESYNC_PASSWORD": "secret123",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        cfg = load_config(spriggler_home)

    assert cfg["meta"]["name"] == "Test Pod"
    assert cfg["devices"]["humidifier"]["driver_config"]["email"] == "test@example.com"
    assert cfg["devices"]["humidifier"]["driver_config"]["password"] == "secret123"


def test_missing_env_var_fails_fast(spriggler_home):
    """Unresolved $VAR raises ConfigError with helpful message."""
    # Ensure the var is NOT set
    env = {k: v for k, v in os.environ.items() if k != "SPRIGGLER_VESYNC_EMAIL"}
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="SPRIGGLER_VESYNC_EMAIL"):
            load_config(spriggler_home)


def test_missing_config_file(tmp_path):
    """Missing config.toml raises ConfigError."""
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path)


def test_malformed_toml(tmp_path):
    """Malformed TOML raises ConfigError."""
    config = tmp_path / "config.toml"
    config.write_text("this is not [valid toml\n")
    with pytest.raises(ConfigError, match="Failed to parse"):
        load_config(tmp_path)


def test_dotenv_loaded(spriggler_home):
    """Secrets from .env file are resolved."""
    dotenv = spriggler_home / ".env"
    dotenv.write_text(
        "SPRIGGLER_VESYNC_EMAIL=from_dotenv@example.com\n"
        "SPRIGGLER_VESYNC_PASSWORD=dotenv_secret\n"
    )
    # Clear these from real env to prove .env is the source
    env = {k: v for k, v in os.environ.items()
           if k not in ("SPRIGGLER_VESYNC_EMAIL", "SPRIGGLER_VESYNC_PASSWORD")}
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load_config(spriggler_home)

    assert cfg["devices"]["humidifier"]["driver_config"]["email"] == "from_dotenv@example.com"


def test_real_env_overrides_dotenv(spriggler_home):
    """Real environment variables take precedence over .env."""
    dotenv = spriggler_home / ".env"
    dotenv.write_text(
        "SPRIGGLER_VESYNC_EMAIL=from_dotenv@example.com\n"
        "SPRIGGLER_VESYNC_PASSWORD=dotenv_secret\n"
    )
    env = {
        "SPRIGGLER_VESYNC_EMAIL": "from_real_env@example.com",
        "SPRIGGLER_VESYNC_PASSWORD": "real_secret",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        cfg = load_config(spriggler_home)

    assert cfg["devices"]["humidifier"]["driver_config"]["email"] == "from_real_env@example.com"


def test_non_var_strings_pass_through(spriggler_home):
    """Strings not starting with $ are left alone."""
    env = {
        "SPRIGGLER_VESYNC_EMAIL": "x@x.com",
        "SPRIGGLER_VESYNC_PASSWORD": "x",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        cfg = load_config(spriggler_home)

    assert cfg["meta"]["name"] == "Test Pod"
    assert cfg["units"]["temperature"] == "F"


def test_metadata_attached(spriggler_home):
    """Loader attaches _home and _config_file metadata."""
    env = {
        "SPRIGGLER_VESYNC_EMAIL": "x@x.com",
        "SPRIGGLER_VESYNC_PASSWORD": "x",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        cfg = load_config(spriggler_home)

    assert cfg["_home"] == spriggler_home
    assert cfg["_config_file"] == spriggler_home / "config.toml"
