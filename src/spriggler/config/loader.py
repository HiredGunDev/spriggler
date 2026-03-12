"""Config loader — reads config.toml, resolves environment variable
references, and returns a validated configuration dictionary.

Usage:
    from spriggler.config.loader import load_config
    cfg = load_config(home_dir)

The loader:
    1. Loads .env from SPRIGGLER_HOME (if present) via python-dotenv
    2. Reads config.toml via stdlib tomllib
    3. Walks the parsed dict and resolves any string value starting
       with "$" as an environment variable reference
    4. Returns the resolved config dict

Environment variable resolution:
    Any string value in config.toml that starts with "$" is treated
    as an environment variable reference.  The "$" prefix is stripped
    and the remainder is looked up in os.environ.

    Example in config.toml:
        email = "$SPRIGGLER_VESYNC_EMAIL"

    Resolves to the value of os.environ["SPRIGGLER_VESYNC_EMAIL"].

    If the variable is not set, the loader raises ConfigError with
    a clear message identifying the config key and the missing
    variable name.  Fail fast, not at 2 AM.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when config loading or validation fails."""
    pass


def load_config(home: Path) -> dict[str, Any]:
    """Load and resolve Spriggler configuration.

    Parameters
    ----------
    home : Path
        Spriggler home directory (e.g., ~/.spriggler).
        Must contain config.toml.  May contain .env.

    Returns
    -------
    dict
        Fully resolved configuration dictionary.

    Raises
    ------
    ConfigError
        If config.toml is missing, unparseable, or contains
        unresolvable environment variable references.
    """
    home = Path(home).expanduser()

    # ── Step 1: Load .env if present ─────────────────────────────
    env_file = home / ".env"
    if env_file.is_file():
        # override=False: real env vars take precedence over .env
        load_dotenv(env_file, override=False)

    # ── Step 2: Read and parse config.toml ───────────────────────
    config_file = home / "config.toml"
    if not config_file.is_file():
        raise ConfigError(
            f"Config file not found: {config_file}\n"
            f"Run 'spriggler config init' to create one, or copy "
            f"your config.toml to {home}/."
        )

    try:
        with open(config_file, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(
            f"Failed to parse {config_file}: {e}"
        ) from e

    # ── Step 3: Resolve environment variable references ──────────
    resolved = _resolve_env_vars(raw, config_path=str(config_file))

    # ── Step 4: Attach metadata ──────────────────────────────────
    resolved["_home"] = home
    resolved["_config_file"] = config_file

    return resolved


def _resolve_env_vars(
    obj: Any,
    config_path: str,
    key_path: str = "",
) -> Any:
    """Recursively walk a parsed config and resolve $VAR references.

    Parameters
    ----------
    obj : Any
        The current node in the config tree.
    config_path : str
        Path to config.toml (for error messages).
    key_path : str
        Dotted path to current position (for error messages).

    Returns
    -------
    Any
        The node with all string $VAR references resolved.
    """
    if isinstance(obj, dict):
        return {
            k: _resolve_env_vars(
                v, config_path, f"{key_path}.{k}" if key_path else k
            )
            for k, v in obj.items()
        }

    if isinstance(obj, list):
        return [
            _resolve_env_vars(
                item, config_path, f"{key_path}[{i}]"
            )
            for i, item in enumerate(obj)
        ]

    if isinstance(obj, str) and obj.startswith("$"):
        var_name = obj[1:]
        value = os.environ.get(var_name)
        if value is None:
            raise ConfigError(
                f"Unresolved environment variable in {config_path}\n"
                f"  Key:      {key_path}\n"
                f"  Value:    {obj}\n"
                f"  Variable: {var_name} is not set\n"
                f"\n"
                f"Set it in ~/.spriggler/.env:\n"
                f"  {var_name}=your_value_here\n"
                f"\n"
                f"Or export it in your shell:\n"
                f"  export {var_name}=your_value_here"
            )
        return value

    # Non-string primitives (int, float, bool, datetime) pass through
    return obj
