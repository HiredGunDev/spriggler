"""Tests for spriggler.physics — temperature and humidity conversions.

Reference values verified against:
  - NOAA humidity calculator
  - Vaisala Humidity Conversion Formulas (Application Note B210973EN)
  - Manual calculation from Magnus formula
"""

import math
import pytest

from spriggler.physics.temperature import (
    fahrenheit_to_kelvin,
    kelvin_to_fahrenheit,
    celsius_to_kelvin,
    kelvin_to_celsius,
    fahrenheit_to_celsius,
    celsius_to_fahrenheit,
)

from spriggler.physics import registry

# Import the plugin to register it
import spriggler.physics.rh_to_ah  # noqa: F401


# ── Temperature conversions ──────────────────────────────────────

class TestTemperature:
    """Temperature unit conversions — pure math, known values."""

    def test_freezing_point(self):
        assert fahrenheit_to_kelvin(32.0) == pytest.approx(273.15)
        assert kelvin_to_fahrenheit(273.15) == pytest.approx(32.0)
        assert celsius_to_kelvin(0.0) == pytest.approx(273.15)
        assert kelvin_to_celsius(273.15) == pytest.approx(0.0)

    def test_boiling_point(self):
        assert fahrenheit_to_kelvin(212.0) == pytest.approx(373.15)
        assert kelvin_to_fahrenheit(373.15) == pytest.approx(212.0)
        assert celsius_to_kelvin(100.0) == pytest.approx(373.15)

    def test_body_temp(self):
        assert fahrenheit_to_kelvin(98.6) == pytest.approx(310.15, abs=0.01)
        assert celsius_to_fahrenheit(37.0) == pytest.approx(98.6)

    def test_grow_environment_range(self):
        """Typical grow pod temperatures."""
        # 75°F — common target
        k = fahrenheit_to_kelvin(75.0)
        assert k == pytest.approx(297.039, abs=0.01)
        assert kelvin_to_fahrenheit(k) == pytest.approx(75.0, abs=0.01)

        # 80°F — seedling lights-on target
        k = fahrenheit_to_kelvin(80.0)
        assert k == pytest.approx(299.817, abs=0.01)

    def test_round_trip_f_k_f(self):
        """Fahrenheit → Kelvin → Fahrenheit should round-trip."""
        for f in [32.0, 72.0, 75.0, 80.0, 98.6, 212.0]:
            assert kelvin_to_fahrenheit(fahrenheit_to_kelvin(f)) == pytest.approx(f)

    def test_round_trip_c_k_c(self):
        """Celsius → Kelvin → Celsius should round-trip."""
        for c in [-40.0, 0.0, 20.0, 25.0, 37.0, 100.0]:
            assert kelvin_to_celsius(celsius_to_kelvin(c)) == pytest.approx(c)

    def test_cross_conversions(self):
        assert fahrenheit_to_celsius(32.0) == pytest.approx(0.0)
        assert fahrenheit_to_celsius(212.0) == pytest.approx(100.0)
        assert celsius_to_fahrenheit(0.0) == pytest.approx(32.0)
        assert celsius_to_fahrenheit(100.0) == pytest.approx(212.0)

    def test_minus_40_crossover(self):
        """-40 is where Fahrenheit and Celsius are equal."""
        assert fahrenheit_to_celsius(-40.0) == pytest.approx(-40.0)
        assert celsius_to_fahrenheit(-40.0) == pytest.approx(-40.0)


# ── RH ↔ Absolute Humidity ───────────────────────────────────────

class TestRhToAh:
    """Relative humidity ↔ absolute humidity via Magnus formula.

    Reference values computed from the Magnus formula with
    Alduchov & Eskridge (1996) constants:
        a = 17.625, b = 243.04°C, c = 610.94 Pa
    """

    def test_plugin_registered(self):
        """The rh_to_ah plugin is in the registry."""
        assert registry.has_plugin("humidity")
        plugin = registry.get("humidity")
        assert plugin.name == "rh_to_ah"
        assert plugin.fundamental_property == "absolute_humidity"
        assert plugin.co_properties == ["temperature"]

    def test_100_percent_at_20c(self):
        """At 100%RH and 20°C, AH ≈ 17.3 g/m³.

        This is a well-known reference point.  Saturation vapor
        pressure at 20°C ≈ 2338 Pa.  AH = (e × Mw) / (R × T).
        """
        temp_k = celsius_to_kelvin(20.0)
        ah = registry.to_fundamental("humidity", 100.0, temperature=temp_k)
        assert ah == pytest.approx(17.3, abs=0.2)

    def test_50_percent_at_20c(self):
        """At 50%RH and 20°C, AH ≈ 8.65 g/m³ (half of saturation)."""
        temp_k = celsius_to_kelvin(20.0)
        ah = registry.to_fundamental("humidity", 50.0, temperature=temp_k)
        assert ah == pytest.approx(8.65, abs=0.15)

    def test_seedling_conditions_day(self):
        """80°F / 80%RH — seedling lights-on target.

        At 80°F (26.67°C), saturation AH ≈ 25.2 g/m³.
        At 80%RH: AH ≈ 20.2 g/m³.
        """
        temp_k = fahrenheit_to_kelvin(80.0)
        ah = registry.to_fundamental("humidity", 80.0, temperature=temp_k)
        assert ah == pytest.approx(20.2, abs=0.5)

    def test_seedling_conditions_night(self):
        """75°F / 70%RH — seedling lights-off target.

        At 75°F (23.89°C), saturation AH ≈ 21.6 g/m³.
        At 70%RH: AH ≈ 15.1 g/m³.
        """
        temp_k = fahrenheit_to_kelvin(75.0)
        ah = registry.to_fundamental("humidity", 70.0, temperature=temp_k)
        assert ah == pytest.approx(15.1, abs=0.5)

    def test_round_trip(self):
        """RH → AH → RH should round-trip at various conditions."""
        test_points = [
            (60.0, fahrenheit_to_kelvin(70.0)),
            (80.0, fahrenheit_to_kelvin(80.0)),
            (45.0, fahrenheit_to_kelvin(65.0)),
            (95.0, celsius_to_kelvin(30.0)),
            (30.0, celsius_to_kelvin(15.0)),
        ]
        for rh, temp_k in test_points:
            ah = registry.to_fundamental("humidity", rh, temperature=temp_k)
            rh_back = registry.to_derived("humidity", ah, temperature=temp_k)
            assert rh_back == pytest.approx(rh, abs=0.01), \
                f"Round-trip failed: {rh}%RH @ {temp_k}K → {ah} g/m³ → {rh_back}%RH"

    def test_heater_phantom_cross_effect(self):
        """Demonstrate the phantom cross-effect that killed v0.4.

        A heater raises temperature but doesn't change moisture.
        %RH drops because the air's capacity increases.
        Absolute humidity stays constant.

        Before heater:  75°F, 70%RH
        After heater:   80°F, %RH drops, but AH is the same.
        """
        temp_before = fahrenheit_to_kelvin(75.0)
        temp_after = fahrenheit_to_kelvin(80.0)

        ah_before = registry.to_fundamental("humidity", 70.0, temperature=temp_before)

        # Same absolute humidity at the higher temperature →
        # lower %RH (phantom cross-effect)
        rh_after = registry.to_derived("humidity", ah_before, temperature=temp_after)
        assert rh_after < 70.0, "RH should drop when temperature rises"
        assert rh_after > 55.0, "RH shouldn't drop unreasonably"

        # But absolute humidity is UNCHANGED — this is the whole point
        ah_after = registry.to_fundamental("humidity", rh_after, temperature=temp_after)
        assert ah_after == pytest.approx(ah_before, abs=0.01), \
            "Absolute humidity must not change when only temperature changes"

    def test_zero_rh(self):
        """0%RH → 0 g/m³ at any temperature."""
        for temp_k in [273.15, 293.15, 313.15]:
            ah = registry.to_fundamental("humidity", 0.0, temperature=temp_k)
            assert ah == pytest.approx(0.0, abs=0.001)

    def test_missing_co_property_raises(self):
        """Calling without temperature raises TypeError."""
        with pytest.raises(TypeError, match="temperature"):
            registry.to_fundamental("humidity", 50.0)

    def test_unknown_property_raises(self):
        """Calling with unknown property raises KeyError."""
        with pytest.raises(KeyError, match="no_such_property"):
            registry.to_fundamental("no_such_property", 50.0)


# ── Registry interface ───────────────────────────────────────────

class TestRegistry:
    """Test the plugin registry interface."""

    def test_list_plugins(self):
        plugins = registry.list_plugins()
        assert len(plugins) >= 1
        names = [p.name for p in plugins]
        assert "rh_to_ah" in names

    def test_is_derived(self):
        assert registry.is_derived("humidity") is True
        assert registry.is_derived("temperature") is False

    def test_fundamental_name(self):
        assert registry.fundamental_name("humidity") == "absolute_humidity"
        # Non-derived property returns itself
        assert registry.fundamental_name("temperature") == "temperature"
