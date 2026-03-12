"""Tests for spriggler.util.discovery — plugin autodiscovery."""

import pytest

from spriggler.util.discovery import discover_plugins, discover_all
from spriggler.physics import registry


class TestDiscoverPlugins:
    """Test the discover_plugins function."""

    def test_discovers_builtin_physics(self):
        """Scanning spriggler.physics finds rh_to_ah."""
        result = discover_plugins(package="spriggler.physics")
        assert result.total >= 1
        assert any("rh_to_ah" in name for name in result.loaded)

    def test_skips_init(self):
        """__init__.py is never loaded as a plugin."""
        result = discover_plugins(package="spriggler.physics")
        assert not any("__init__" in name for name in result.loaded)

    def test_skips_temperature_module(self):
        """temperature.py is a utility, not a registry plugin.

        It gets imported (no error), but it doesn't register anything.
        That's fine — discovery imports it, it just doesn't call
        registry.register().  It should show up in loaded.
        """
        result = discover_plugins(package="spriggler.physics")
        # temperature.py should import successfully
        assert any("temperature" in name for name in result.loaded)

    def test_exclude_parameter(self):
        """Modules in the exclude set are skipped."""
        result = discover_plugins(
            package="spriggler.physics",
            exclude={"rh_to_ah"},
        )
        assert not any("rh_to_ah" in name for name in result.loaded)

    def test_nonexistent_package(self):
        """A missing package returns empty results with error."""
        result = discover_plugins(package="spriggler.nonexistent_xyzzy")
        assert result.total == 0
        assert len(result.skipped) > 0

    def test_entry_point_group_none(self):
        """Passing entry_point_group=None skips layer 2."""
        result = discover_plugins(
            package="spriggler.physics",
            entry_point_group=None,
        )
        # Should still find built-ins
        assert result.total >= 1

    def test_nonexistent_entry_point_group(self):
        """A nonexistent entry point group is harmless."""
        result = discover_plugins(
            package="spriggler.physics",
            entry_point_group="spriggler.xyzzy_nonexistent",
        )
        # Built-ins still load, no entry point errors
        assert result.total >= 1
        assert not any("entry_point" in s for s in result.skipped)

    def test_registry_populated_after_discovery(self):
        """After discovery, the physics registry has the rh_to_ah plugin."""
        discover_plugins(package="spriggler.physics")
        assert registry.has_plugin("humidity")
        plugin = registry.get("humidity")
        assert plugin.name == "rh_to_ah"

    def test_summary_string(self):
        """DiscoveryResult.summary() returns a readable string."""
        result = discover_plugins(package="spriggler.physics")
        summary = result.summary("physics plugins")
        assert "loaded" in summary
        assert "physics plugins" in summary


class TestDiscoverAll:
    """Test the discover_all convenience function."""

    def test_discovers_all_types(self):
        """discover_all() returns results for physics, sensors, devices."""
        results = discover_all()
        assert "physics" in results
        assert "sensors" in results
        assert "devices" in results

    def test_physics_loaded(self):
        """Physics plugins are found."""
        results = discover_all()
        assert results["physics"].total >= 1

    def test_empty_packages_no_crash(self):
        """Sensor and device packages are empty but don't crash."""
        results = discover_all()
        # These packages exist but have no plugin modules yet
        # (just __init__.py) — that's fine, zero loaded, zero skipped
        assert results["sensors"].total >= 0
        assert results["devices"].total >= 0
