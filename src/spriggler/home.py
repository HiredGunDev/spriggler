"""Spriggler home directory resolution and daemon detection.

The home directory is the root of a Spriggler installation. It contains:
    config/config.json   - user configuration
    calibration/         - learned coefficients
    status.json          - daemon heartbeat (written every cycle)
    logs/                - structured logs

Resolution order:
    1. --home flag (if provided)
    2. SPRIGGLER_HOME environment variable
    3. Current working directory
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


class HomeNotFoundError(Exception):
    """Raised when the home directory cannot be resolved."""
    pass


class ConfigNotFoundError(Exception):
    """Raised when config/config.json is missing from home."""
    pass


@dataclass
class DaemonStatus:
    """Status of the Spriggler daemon."""
    running: bool
    last_seen: float | None = None  # Unix timestamp
    cycle: int | None = None
    status_path: str | None = None


def resolve_home(home_arg: str | None = None) -> Path:
    """Resolve the Spriggler home directory.

    Args:
        home_arg: Explicit path from --home flag. Takes priority.

    Returns:
        Path to the Spriggler home directory.

    Raises:
        HomeNotFoundError: If no valid home directory can be found.
    """
    # 1. Explicit --home flag
    if home_arg is not None:
        p = Path(home_arg).resolve()
        if not p.is_dir():
            raise HomeNotFoundError(f"--home directory does not exist: {p}")
        return p

    # 2. SPRIGGLER_HOME environment variable
    env = os.environ.get('SPRIGGLER_HOME')
    if env is not None:
        p = Path(env).resolve()
        if not p.is_dir():
            raise HomeNotFoundError(
                f"SPRIGGLER_HOME directory does not exist: {p}"
            )
        return p

    # 3. Current working directory
    return Path.cwd()


def resolve_config(home: Path) -> Path:
    """Find config.json within the home directory.

    Args:
        home: The Spriggler home directory.

    Returns:
        Path to config/config.json.

    Raises:
        ConfigNotFoundError: If config file is missing.
    """
    config_path = home / 'config' / 'config.json'
    if not config_path.is_file():
        raise ConfigNotFoundError(
            f"Config not found: {config_path}\n"
            f"Expected config/config.json in Spriggler home: {home}"
        )
    return config_path


def check_daemon(home: Path, max_age_seconds: float = 300.0) -> DaemonStatus:
    """Check whether the Spriggler daemon is running.

    Reads status.json and checks if its timestamp is recent.

    Args:
        home: The Spriggler home directory.
        max_age_seconds: Maximum age of status.json timestamp before
            the daemon is considered dead. Default 5 minutes.

    Returns:
        DaemonStatus with running=True if daemon appears alive.
    """
    status_path = home / 'status.json'

    if not status_path.is_file():
        return DaemonStatus(running=False, status_path=str(status_path))

    try:
        with open(status_path) as f:
            status = json.load(f)
    except (json.JSONDecodeError, OSError):
        return DaemonStatus(running=False, status_path=str(status_path))

    timestamp = status.get('timestamp')
    if timestamp is None:
        return DaemonStatus(running=False, status_path=str(status_path))

    # Parse ISO timestamp to unix time
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(timestamp)
        last_seen = dt.timestamp()
    except (ValueError, TypeError):
        return DaemonStatus(running=False, status_path=str(status_path))

    age = time.time() - last_seen
    running = age < max_age_seconds

    return DaemonStatus(
        running=running,
        last_seen=last_seen,
        cycle=status.get('cycle'),
        status_path=str(status_path),
    )