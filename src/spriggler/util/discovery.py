"""Plugin autodiscovery — scans packages and entry points for plugins.

Two-layer discovery:

  Layer 1 — Built-in: Scan a package directory for .py modules,
  import each one.  Modules self-register with their registry at
  import time.  This covers everything shipped with Spriggler.

  Layer 2 — Third-party: Discover entry points in a named group
  (e.g., "spriggler.physics") and import them.  This covers
  plugins installed via pip from separate packages.

Import errors are caught per-module and logged as warnings.
A broken plugin never crashes the system.

Usage:
    from spriggler.util.discovery import discover_plugins
    from spriggler.physics import registry

    # Discovers built-in + third-party physics plugins
    loaded, skipped = discover_plugins(
        package="spriggler.physics",
        entry_point_group="spriggler.physics",
    )
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from importlib.metadata import entry_points

log = logging.getLogger("spriggler.discovery")


@dataclass
class DiscoveryResult:
    """Result of a plugin discovery scan."""
    loaded: list[str]     # Successfully imported module names
    skipped: list[str]    # Modules that failed to import (with reason)

    @property
    def total(self) -> int:
        return len(self.loaded)

    def summary(self, label: str = "plugins") -> str:
        """Human-readable summary for logging."""
        parts = [f"{self.total} {label} loaded"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return ", ".join(parts)


def discover_plugins(
    package: str,
    entry_point_group: str | None = None,
    exclude: set[str] | None = None,
) -> DiscoveryResult:
    """Discover and import plugins from a package and entry points.

    Parameters
    ----------
    package : str
        Dotted package name to scan (e.g., "spriggler.physics").
        All .py modules in this package (except __init__.py and
        those in `exclude`) are imported.
    entry_point_group : str, optional
        Entry point group name for third-party discovery
        (e.g., "spriggler.physics").  If None, skips layer 2.
    exclude : set[str], optional
        Module names to skip (e.g., {"__init__"}).  The __init__
        module is always excluded.

    Returns
    -------
    DiscoveryResult
        Lists of loaded and skipped module names.
    """
    exclude = exclude or set()
    exclude.add("__init__")

    loaded = []
    skipped = []

    # ── Layer 1: Built-in package scan ───────────────────────────
    try:
        pkg = importlib.import_module(package)
    except ImportError as e:
        log.error("Cannot import package '%s': %s", package, e)
        return DiscoveryResult(loaded=loaded, skipped=[f"{package}: {e}"])

    if not hasattr(pkg, "__path__"):
        log.warning("'%s' is not a package (no __path__), skipping scan", package)
    else:
        for finder, module_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
            if module_name in exclude:
                continue
            if is_pkg:
                # Don't recurse into subpackages — plugins are flat modules
                continue

            full_name = f"{package}.{module_name}"
            try:
                importlib.import_module(full_name)
                loaded.append(full_name)
                log.debug("Loaded built-in plugin: %s", full_name)
            except Exception as e:
                reason = f"{full_name}: {e}"
                skipped.append(reason)
                log.warning("Skipped plugin %s: %s", full_name, e)

    # ── Layer 2: Entry point discovery ───────────────────────────
    if entry_point_group:
        try:
            eps = entry_points()
            # Python 3.12+: entry_points() returns a SelectableGroups
            # or dict-like; use .select() or .get()
            if hasattr(eps, "select"):
                group_eps = eps.select(group=entry_point_group)
            elif isinstance(eps, dict):
                group_eps = eps.get(entry_point_group, [])
            else:
                group_eps = []

            for ep in group_eps:
                # Skip if the module was already loaded in layer 1
                # (avoids double-registration of built-ins that also
                # declare entry points)
                if ep.value.rsplit(":", 1)[0] in [m for m in loaded]:
                    log.debug("Entry point '%s' already loaded, skipping", ep.name)
                    continue

                try:
                    ep.load()
                    loaded.append(f"{entry_point_group}:{ep.name}")
                    log.debug("Loaded entry point plugin: %s (%s)",
                              ep.name, ep.value)
                except Exception as e:
                    reason = f"entry_point:{ep.name}: {e}"
                    skipped.append(reason)
                    log.warning("Skipped entry point plugin '%s': %s",
                                ep.name, e)

        except Exception as e:
            log.warning("Entry point discovery failed for group '%s': %s",
                        entry_point_group, e)

    return DiscoveryResult(loaded=loaded, skipped=skipped)


def discover_all() -> dict[str, DiscoveryResult]:
    """Discover all plugin types — physics, sensors, devices.

    Returns a dict mapping plugin type to its discovery result.
    Call this once at startup.
    """
    results = {}

    results["physics"] = discover_plugins(
        package="spriggler.physics",
        entry_point_group="spriggler.physics",
    )

    results["sensors"] = discover_plugins(
        package="spriggler.sensors",
        entry_point_group="spriggler.sensors",
    )

    results["devices"] = discover_plugins(
        package="spriggler.devices",
        entry_point_group="spriggler.devices",
    )

    for plugin_type, result in results.items():
        if result.loaded:
            log.info("Discovery [%s]: %s", plugin_type,
                     result.summary(plugin_type))
        if result.skipped:
            for skip in result.skipped:
                log.warning("Discovery [%s] skipped: %s",
                            plugin_type, skip)

    return results
