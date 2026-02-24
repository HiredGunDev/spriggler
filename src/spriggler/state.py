"""State directory resolution for Spriggler.

The state directory is where the daemon writes its output and where
CLI tools read from. One directory, one source of truth.

Contents:
    status.json         Current state (atomic write each cycle)
    spriggler.log       Structured JSON-lines event log
    calibration.json    Learned thermal models (future)

Resolution order:
    1. --state-dir flag (daemon and CLI both accept it)
    2. SPRIGGLER_STATE_DIR environment variable
    3. Default: ~/.spriggler/

The directory is created on first use if it doesn't exist.
"""

import os
from pathlib import Path


DEFAULT_STATE_DIR = Path.home() / '.spriggler'


def resolve_state_dir(override: str | None = None) -> Path:
    """Resolve the state directory path.

    Args:
        override: Explicit path from --state-dir flag. Takes priority.

    Returns:
        Resolved, absolute Path to the state directory.
    """
    if override:
        state_dir = Path(override).resolve()
    elif 'SPRIGGLER_STATE_DIR' in os.environ:
        state_dir = Path(os.environ['SPRIGGLER_STATE_DIR']).resolve()
    else:
        state_dir = DEFAULT_STATE_DIR

    return state_dir


def ensure_state_dir(state_dir: Path) -> Path:
    """Create the state directory if it doesn't exist.

    Returns the path for convenience.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir