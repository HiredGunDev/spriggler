"""Unit conversion between user units and SI.

Spriggler stores and computes everything in SI internally:
    - Temperature: Kelvin (K)
    - Humidity: %RH (already dimensionless, no conversion needed)

User-facing values (config, logs, alerts) use the configured unit.
Conversion happens at two boundaries:
    1. Config load: user units → SI (once, at startup)
    2. Display: SI → user units (logs, alerts, UI)

This module provides the conversion functions and a config converter
that walks the config dict and converts all temperature values to SI.
"""

import copy


def f_to_k(f: float) -> float:
    """Fahrenheit to Kelvin."""
    return (f - 32) * 5 / 9 + 273.15


def c_to_k(c: float) -> float:
    """Celsius to Kelvin."""
    return c + 273.15


def k_to_f(k: float) -> float:
    """Kelvin to Fahrenheit."""
    return (k - 273.15) * 9 / 5 + 32


def k_to_c(k: float) -> float:
    """Kelvin to Celsius."""
    return k - 273.15


def to_kelvin(value: float, unit: str) -> float:
    """Convert a temperature from the given unit to Kelvin.

    Args:
        value: Temperature value.
        unit: 'F', 'C', or 'K'.

    Returns:
        Temperature in Kelvin.
    """
    if unit == 'F':
        return f_to_k(value)
    elif unit == 'C':
        return c_to_k(value)
    elif unit == 'K':
        return value
    else:
        raise ValueError(f"Unknown temperature unit: '{unit}'")


def from_kelvin(value: float, unit: str) -> float:
    """Convert a temperature from Kelvin to the given unit.

    Args:
        value: Temperature in Kelvin.
        unit: 'F', 'C', or 'K'.

    Returns:
        Temperature in the requested unit.
    """
    if unit == 'F':
        return k_to_f(value)
    elif unit == 'C':
        return k_to_c(value)
    elif unit == 'K':
        return value
    else:
        raise ValueError(f"Unknown temperature unit: '{unit}'")


def format_temp(kelvin: float, unit: str, precision: int = 1) -> str:
    """Format a Kelvin temperature for human display with unit label.

    Args:
        kelvin: Temperature in Kelvin.
        unit: Display unit ('F', 'C', or 'K').
        precision: Decimal places.

    Returns:
        Formatted string like "75.0°F" or "23.9°C".
    """
    converted = from_kelvin(kelvin, unit)
    return f"{converted:.{precision}f} {unit}"


def convert_config_to_si(config: dict) -> dict:
    """Convert all temperature values in a config dict from user units to SI.

    This creates a deep copy — the original config is not modified.
    After conversion, config['units']['temperature'] is set to 'K'
    and config['_original_unit'] preserves the user's preference
    for display formatting.

    Converts:
        - schedules.*.phases.*.targets.temperature.{min, max, ideal}
        - safety.environments.*.limits.temperature.{absolute_min, absolute_max}
        - safety.environments.*.rate_of_change.temperature.{max_per_minute}

    Args:
        config: Validated config dict with user units.

    Returns:
        New config dict with all temperatures in Kelvin.
    """
    cfg = copy.deepcopy(config)
    unit = cfg['units']['temperature']

    if unit == 'K':
        # Already SI — just tag it
        cfg['_original_unit'] = 'K'
        return cfg

    # Convert schedule targets
    for env_id, schedule in cfg.get('schedules', {}).items():
        for phase in schedule.get('phases', []):
            temp_target = phase.get('targets', {}).get('temperature')
            if temp_target:
                if 'min' in temp_target:
                    temp_target['min'] = to_kelvin(temp_target['min'], unit)
                if 'max' in temp_target:
                    temp_target['max'] = to_kelvin(temp_target['max'], unit)
                if 'ideal' in temp_target:
                    temp_target['ideal'] = to_kelvin(temp_target['ideal'], unit)

    # Convert safety limits
    for env_id, env_safety in cfg.get('safety', {}).get('environments', {}).items():
        temp_limits = env_safety.get('limits', {}).get('temperature')
        if temp_limits:
            if 'absolute_min' in temp_limits:
                temp_limits['absolute_min'] = to_kelvin(temp_limits['absolute_min'], unit)
            if 'absolute_max' in temp_limits:
                temp_limits['absolute_max'] = to_kelvin(temp_limits['absolute_max'], unit)

        # Convert rate of change thresholds
        # Rate is °/minute — need to convert the magnitude
        # A rate of 2°F/min = 2 × 5/9 = 1.11 K/min
        temp_rate = env_safety.get('rate_of_change', {}).get('temperature')
        if temp_rate and 'max_per_minute' in temp_rate:
            if unit == 'F':
                temp_rate['max_per_minute'] = temp_rate['max_per_minute'] * 5 / 9
            # Celsius to Kelvin rate is 1:1 (same scale)

    # Mark the conversion
    cfg['_original_unit'] = unit
    cfg['units']['temperature'] = 'K'

    return cfg

