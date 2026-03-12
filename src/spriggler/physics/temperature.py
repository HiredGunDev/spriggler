"""Temperature unit conversions.

Temperature is a fundamental quantity — no physics plugin needed.
These are pure unit conversions for the sensor boundary and display.

Internal representation: Kelvin (SI)
Display/config units: Fahrenheit or Celsius (declared in config)
Sensor units: varies (Govee reports Celsius natively, displayed as °F)

All functions are pure — no state, no side effects.
"""


def fahrenheit_to_kelvin(f: float) -> float:
    """Convert Fahrenheit to Kelvin."""
    return (f - 32.0) * 5.0 / 9.0 + 273.15


def kelvin_to_fahrenheit(k: float) -> float:
    """Convert Kelvin to Fahrenheit."""
    return (k - 273.15) * 9.0 / 5.0 + 32.0


def celsius_to_kelvin(c: float) -> float:
    """Convert Celsius to Kelvin."""
    return c + 273.15


def kelvin_to_celsius(k: float) -> float:
    """Convert Kelvin to Celsius."""
    return k - 273.15


def fahrenheit_to_celsius(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32.0) * 5.0 / 9.0


def celsius_to_fahrenheit(c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return c * 9.0 / 5.0 + 32.0
