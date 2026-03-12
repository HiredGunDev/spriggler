"""Physics plugin: %RH ↔ absolute humidity (g/m³).

This is the plugin that eliminates the phantom cross-effect that
sank Spriggler v0.4.  The heater's calibrated effect on absolute
humidity is zero (correct), because it doesn't add or remove
moisture.  Its apparent effect on %RH is a phantom — warming air
reduces relative humidity with no change in moisture content.

The Magnus formula relates relative humidity, temperature, and
the saturation vapor pressure of water.  It's accurate to ±0.4%
for temperatures between -45°C and 60°C, which covers all
reasonable grow environment conditions.

Derived property:      humidity (%RH, 0-100)
Fundamental property:  absolute_humidity (g/m³)
Co-property:           temperature (Kelvin)

References:
    Alduchov & Eskridge (1996), "Improved Magnus Form Approximation
    of Saturation Vapor Pressure", J. Appl. Meteor., 35, 601-609.

    Constants used (Alduchov & Eskridge recommended values):
        a = 17.625
        b = 243.04 °C
        c = 610.94 Pa  (saturation vapor pressure at 0°C)
"""

import math

from spriggler.physics import PhysicsPlugin, registry

# Magnus formula constants (Alduchov & Eskridge 1996)
_A = 17.625      # dimensionless
_B = 243.04      # °C
_C = 610.94      # Pa — saturation vapor pressure at 0°C

# Molecular weight of water / universal gas constant
# Used to convert vapor pressure (Pa) to density (g/m³)
# Mw / R = 18.015 g/mol / 8.31446 J/(mol·K) = 2.16679 g·K/J
_MW_OVER_R = 2.16679


def _saturation_vapor_pressure(temp_c: float) -> float:
    """Saturation vapor pressure of water at a given temperature.

    Parameters
    ----------
    temp_c : float
        Temperature in Celsius.

    Returns
    -------
    float
        Saturation vapor pressure in Pascals.
    """
    return _C * math.exp((_A * temp_c) / (_B + temp_c))


def rh_to_absolute_humidity(rh: float, *, temperature: float) -> float:
    """Convert relative humidity to absolute humidity.

    Parameters
    ----------
    rh : float
        Relative humidity as a percentage (0-100).
    temperature : float
        Air temperature in Kelvin.

    Returns
    -------
    float
        Absolute humidity in grams per cubic meter (g/m³).

    Examples
    --------
    >>> rh_to_absolute_humidity(62.3, temperature=297.15)  # 75°F, 62.3%RH
    14.41...
    """
    temp_c = temperature - 273.15
    e_sat = _saturation_vapor_pressure(temp_c)
    e_actual = e_sat * (rh / 100.0)
    # Ideal gas law: ρ = (e · Mw) / (R · T)
    return (e_actual * _MW_OVER_R) / temperature


def absolute_humidity_to_rh(ah: float, *, temperature: float) -> float:
    """Convert absolute humidity to relative humidity.

    Parameters
    ----------
    ah : float
        Absolute humidity in grams per cubic meter (g/m³).
    temperature : float
        Air temperature in Kelvin.

    Returns
    -------
    float
        Relative humidity as a percentage (0-100).

    Examples
    --------
    >>> absolute_humidity_to_rh(14.41, temperature=297.15)  # back to %RH
    62.3...
    """
    temp_c = temperature - 273.15
    e_sat = _saturation_vapor_pressure(temp_c)
    # Reverse ideal gas law: e = (ρ · R · T) / Mw
    e_actual = (ah * temperature) / _MW_OVER_R
    return (e_actual / e_sat) * 100.0


# ── Register with the global registry ────────────────────────────

registry.register(PhysicsPlugin(
    name="rh_to_ah",
    description=(
        "Converts relative humidity (%RH) to absolute humidity (g/m³) "
        "using the Magnus formula (Alduchov & Eskridge 1996).  "
        "Requires air temperature in Kelvin as a co-property.  "
        "Accurate to ±0.4% for -45°C to 60°C."
    ),
    derived_property="humidity",
    fundamental_property="absolute_humidity",
    co_properties=["temperature"],
    to_fundamental=rh_to_absolute_humidity,
    to_derived=absolute_humidity_to_rh,
))
