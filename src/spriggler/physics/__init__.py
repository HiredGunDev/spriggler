"""Physics plugin registry — converts between derived and fundamental quantities.

The controller works internally in fundamental physical quantities:
  - Temperature in Kelvin
  - Moisture as absolute humidity (g/m³)

Sensors often report derived quantities:
  - Temperature in °F or °C
  - Moisture as relative humidity (%RH)

Physics plugins handle these conversions at the sensor boundary.
Each plugin declares:
  - derived_property:     the sensor-reported quantity (e.g., "humidity")
  - fundamental_property: the internal quantity (e.g., "absolute_humidity")
  - co_properties:        other fundamentals needed (e.g., temperature in K)
  - to_fundamental():     derived + co-properties → fundamental
  - to_derived():         fundamental + co-properties → derived

Plugins self-register via registry.register().
Import the plugin module to activate it.

Usage:
    from spriggler.physics import registry
    import spriggler.physics.rh_to_ah  # activates the plugin

    # At sensor boundary (incoming reading):
    ah = registry.to_fundamental("humidity", 62.3, temperature=297.15)

    # For display (outgoing to user):
    rh = registry.to_derived("humidity", 14.8, temperature=297.15)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class PhysicsPlugin:
    """A registered property conversion plugin."""

    name: str
    description: str

    # The derived property name as it appears in sensor reports
    # and config (e.g., "humidity")
    derived_property: str

    # The fundamental property the controller uses internally
    # (e.g., "absolute_humidity")
    fundamental_property: str

    # derived + co-properties → fundamental
    # Signature: (derived_value, **co_property_values) → fundamental_value
    to_fundamental: Callable[..., float] = field(repr=False)

    # fundamental + co-properties → derived
    # Signature: (fundamental_value, **co_property_values) → derived_value
    to_derived: Callable[..., float] = field(repr=False)

    # Other fundamental properties required for conversion
    # (e.g., ["temperature"] — must be in Kelvin)
    co_properties: list[str] = field(default_factory=list)


class PluginRegistry:
    """Registry of physics conversion plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, PhysicsPlugin] = {}

    def register(self, plugin: PhysicsPlugin) -> None:
        """Register a physics plugin.

        If a plugin for the same derived_property already exists,
        it is replaced (allows overriding defaults).
        """
        self._plugins[plugin.derived_property] = plugin

    def get(self, derived_property: str) -> PhysicsPlugin | None:
        """Get the plugin for a derived property, or None."""
        return self._plugins.get(derived_property)

    def has_plugin(self, derived_property: str) -> bool:
        """Check if a plugin exists for a derived property."""
        return derived_property in self._plugins

    def list_plugins(self) -> list[PhysicsPlugin]:
        """Return all registered plugins."""
        return list(self._plugins.values())

    def to_fundamental(self, derived_property: str, derived_value: float,
                       **co_properties: float) -> float:
        """Convert a derived value to its fundamental equivalent.

        Parameters
        ----------
        derived_property : str
            The property name (e.g., "humidity").
        derived_value : float
            The derived value (e.g., 62.3 for %RH).
        **co_properties : float
            Co-property values in fundamental units
            (e.g., temperature=297.15 for Kelvin).

        Returns
        -------
        float
            The fundamental value (e.g., g/m³).

        Raises
        ------
        KeyError
            If no plugin exists for the derived property.
        TypeError
            If required co-properties are missing.
        """
        plugin = self._plugins.get(derived_property)
        if plugin is None:
            raise KeyError(
                f"No physics plugin for derived property '{derived_property}'. "
                f"Available: {list(self._plugins.keys())}"
            )
        missing = [p for p in plugin.co_properties if p not in co_properties]
        if missing:
            raise TypeError(
                f"Plugin '{plugin.name}' requires co-properties {plugin.co_properties}, "
                f"missing: {missing}"
            )
        return plugin.to_fundamental(derived_value, **co_properties)

    def to_derived(self, derived_property: str, fundamental_value: float,
                   **co_properties: float) -> float:
        """Convert a fundamental value back to derived.

        Parameters
        ----------
        derived_property : str
            The property name (e.g., "humidity").
        fundamental_value : float
            The fundamental value (e.g., 14.8 for g/m³).
        **co_properties : float
            Co-property values in fundamental units.

        Returns
        -------
        float
            The derived value (e.g., %RH).
        """
        plugin = self._plugins.get(derived_property)
        if plugin is None:
            raise KeyError(
                f"No physics plugin for derived property '{derived_property}'. "
                f"Available: {list(self._plugins.keys())}"
            )
        missing = [p for p in plugin.co_properties if p not in co_properties]
        if missing:
            raise TypeError(
                f"Plugin '{plugin.name}' requires co-properties {plugin.co_properties}, "
                f"missing: {missing}"
            )
        return plugin.to_derived(fundamental_value, **co_properties)

    def is_derived(self, property_name: str) -> bool:
        """Check if a property name is a known derived quantity."""
        return property_name in self._plugins

    def fundamental_name(self, derived_property: str) -> str:
        """Get the fundamental property name for a derived property.

        Returns the input unchanged if it's not a known derived property
        (assumed to be fundamental already).
        """
        plugin = self._plugins.get(derived_property)
        if plugin is None:
            return derived_property
        return plugin.fundamental_property


# ── Global registry instance ─────────────────────────────────────
# Populated automatically by spriggler.util.discovery at startup.
# Plugin modules in this package self-register when imported.

registry = PluginRegistry()
