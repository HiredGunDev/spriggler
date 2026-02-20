"""Tests for unit conversion and SI config normalization.

Verifies:
    - Basic temperature conversions (F↔K, C↔K)
    - Config conversion preserves structure, converts temperatures
    - Config loader returns SI values
    - Changing user unit doesn't affect internal values
    - Display formatting includes unit labels
"""

import copy
import pytest

from spriggler.units import (
    f_to_k, c_to_k, k_to_f, k_to_c,
    to_kelvin, from_kelvin, format_temp,
    convert_config_to_si,
)
from spriggler.config.loader import load_config


# ── Basic conversions ────────────────────────────────────────────────────────

class TestBasicConversions:

    def test_freezing_f_to_k(self):
        assert f_to_k(32) == pytest.approx(273.15)

    def test_boiling_f_to_k(self):
        assert f_to_k(212) == pytest.approx(373.15)

    def test_room_temp_f_to_k(self):
        """75°F = 23.89°C = 297.04K"""
        assert f_to_k(75) == pytest.approx(297.0389, abs=0.01)

    def test_freezing_c_to_k(self):
        assert c_to_k(0) == pytest.approx(273.15)

    def test_boiling_c_to_k(self):
        assert c_to_k(100) == pytest.approx(373.15)

    def test_room_temp_c_to_k(self):
        assert c_to_k(23.89) == pytest.approx(297.04, abs=0.01)

    def test_k_to_f_roundtrip(self):
        """F → K → F should be identity."""
        assert k_to_f(f_to_k(75)) == pytest.approx(75)

    def test_k_to_c_roundtrip(self):
        """C → K → C should be identity."""
        assert k_to_c(c_to_k(23.89)) == pytest.approx(23.89)

    def test_to_kelvin_from_f(self):
        assert to_kelvin(75, 'F') == pytest.approx(f_to_k(75))

    def test_to_kelvin_from_c(self):
        assert to_kelvin(23.89, 'C') == pytest.approx(c_to_k(23.89))

    def test_to_kelvin_from_k(self):
        assert to_kelvin(297.04, 'K') == pytest.approx(297.04)

    def test_from_kelvin_to_f(self):
        assert from_kelvin(297.04, 'F') == pytest.approx(75, abs=0.1)

    def test_from_kelvin_to_c(self):
        assert from_kelvin(297.04, 'C') == pytest.approx(23.89, abs=0.01)

    def test_from_kelvin_to_k(self):
        assert from_kelvin(297.04, 'K') == pytest.approx(297.04)

    def test_unknown_unit_raises(self):
        with pytest.raises(ValueError):
            to_kelvin(75, 'R')  # Rankine? No thanks.


# ── Display formatting ───────────────────────────────────────────────────────

class TestFormatting:

    def test_format_fahrenheit(self):
        result = format_temp(297.04, 'F')
        assert ' F' in result
        assert '75' in result

    def test_format_celsius(self):
        result = format_temp(297.04, 'C')
        assert ' C' in result
        assert '23.9' in result

    def test_format_kelvin(self):
        result = format_temp(297.04, 'K')
        assert ' K' in result
        assert '297.0' in result

    def test_format_precision(self):
        result = format_temp(297.04, 'F', precision=2)
        assert ' F' in result


# ── Config conversion ────────────────────────────────────────────────────────

@pytest.fixture
def fahrenheit_config():
    """A minimal config in Fahrenheit."""
    return {
        "version": "0.3",
        "name": "Test",
        "units": {"temperature": "F"},
        "environments": {
            "chamber": {"description": "Test"}
        },
        "sensors": {
            "temp": {
                "driver": "mock", "environment": "chamber",
                "properties": ["temperature"], "driver_config": {}
            },
            "ambient": {
                "driver": "mock", "environment": "ambient",
                "properties": ["temperature"], "driver_config": {}
            },
        },
        "devices": {
            "heater": {
                "driver": "mock", "environment": "chamber",
                "circuit": "main", "role": "heater", "driver_config": {}
            }
        },
        "circuits": {"main": {"max_amps": 20, "voltage": 120}},
        "schedules": {
            "chamber": {
                "phases": [{
                    "name": "day",
                    "start": "00:00",
                    "end": "00:00",
                    "targets": {
                        "temperature": {"min": 70, "max": 80, "ideal": 75},
                        "humidity": {"min": 50, "max": 70, "ideal": 60}
                    }
                }]
            }
        },
        "safety": {
            "environments": {
                "chamber": {
                    "limits": {
                        "temperature": {"absolute_min": 40, "absolute_max": 110}
                    },
                    "rate_of_change": {
                        "temperature": {"max_per_minute": 2.0}
                    }
                }
            },
            "devices": {
                "heater": {"safe_state": "off"}
            },
            "sensor_stale_after_missed": 3,
            "safety_loop_interval_seconds": 15,
        }
    }


@pytest.fixture
def celsius_config(fahrenheit_config):
    """Same config but in Celsius."""
    cfg = copy.deepcopy(fahrenheit_config)
    cfg['units']['temperature'] = 'C'
    # Equivalent values in Celsius
    phase = cfg['schedules']['chamber']['phases'][0]
    phase['targets']['temperature'] = {"min": 21.11, "max": 26.67, "ideal": 23.89}
    limits = cfg['safety']['environments']['chamber']['limits']
    limits['temperature'] = {"absolute_min": 4.44, "absolute_max": 43.33}
    rate = cfg['safety']['environments']['chamber']['rate_of_change']
    rate['temperature'] = {"max_per_minute": 1.111}
    return cfg


class TestConfigConversion:

    def test_fahrenheit_targets_converted_to_kelvin(self, fahrenheit_config):
        converted = convert_config_to_si(fahrenheit_config)
        target = converted['schedules']['chamber']['phases'][0]['targets']['temperature']
        assert target['min'] == pytest.approx(f_to_k(70))
        assert target['max'] == pytest.approx(f_to_k(80))
        assert target['ideal'] == pytest.approx(f_to_k(75))

    def test_fahrenheit_limits_converted_to_kelvin(self, fahrenheit_config):
        converted = convert_config_to_si(fahrenheit_config)
        limits = converted['safety']['environments']['chamber']['limits']['temperature']
        assert limits['absolute_min'] == pytest.approx(f_to_k(40))
        assert limits['absolute_max'] == pytest.approx(f_to_k(110))

    def test_fahrenheit_rate_converted(self, fahrenheit_config):
        """2°F/min = 2 × 5/9 ≈ 1.111 K/min."""
        converted = convert_config_to_si(fahrenheit_config)
        rate = converted['safety']['environments']['chamber']['rate_of_change']['temperature']
        assert rate['max_per_minute'] == pytest.approx(2.0 * 5 / 9)

    def test_humidity_not_converted(self, fahrenheit_config):
        """Humidity (%RH) needs no conversion."""
        converted = convert_config_to_si(fahrenheit_config)
        target = converted['schedules']['chamber']['phases'][0]['targets']['humidity']
        assert target['min'] == 50
        assert target['max'] == 70
        assert target['ideal'] == 60

    def test_original_unit_preserved(self, fahrenheit_config):
        converted = convert_config_to_si(fahrenheit_config)
        assert converted['_original_unit'] == 'F'
        assert converted['units']['temperature'] == 'K'

    def test_original_config_not_mutated(self, fahrenheit_config):
        original_min = fahrenheit_config['schedules']['chamber']['phases'][0]['targets']['temperature']['min']
        convert_config_to_si(fahrenheit_config)
        assert fahrenheit_config['schedules']['chamber']['phases'][0]['targets']['temperature']['min'] == original_min

    def test_unit_field_set_to_k(self, fahrenheit_config):
        converted = convert_config_to_si(fahrenheit_config)
        assert converted['units']['temperature'] == 'K'


class TestUnitIndependence:
    """The critical test: F and C configs produce the same internal values."""

    def test_fahrenheit_and_celsius_produce_same_si(self, fahrenheit_config, celsius_config):
        """A config in °F and the equivalent config in °C must produce
        identical SI values after conversion. This is the whole point."""
        f_converted = convert_config_to_si(fahrenheit_config)
        c_converted = convert_config_to_si(celsius_config)

        f_target = f_converted['schedules']['chamber']['phases'][0]['targets']['temperature']
        c_target = c_converted['schedules']['chamber']['phases'][0]['targets']['temperature']

        assert f_target['min'] == pytest.approx(c_target['min'], abs=0.1)
        assert f_target['max'] == pytest.approx(c_target['max'], abs=0.1)
        assert f_target['ideal'] == pytest.approx(c_target['ideal'], abs=0.1)

    def test_fahrenheit_and_celsius_same_limits(self, fahrenheit_config, celsius_config):
        f_converted = convert_config_to_si(fahrenheit_config)
        c_converted = convert_config_to_si(celsius_config)

        f_limits = f_converted['safety']['environments']['chamber']['limits']['temperature']
        c_limits = c_converted['safety']['environments']['chamber']['limits']['temperature']

        assert f_limits['absolute_min'] == pytest.approx(c_limits['absolute_min'], abs=0.1)
        assert f_limits['absolute_max'] == pytest.approx(c_limits['absolute_max'], abs=0.1)

    def test_fahrenheit_and_celsius_same_rate(self, fahrenheit_config, celsius_config):
        f_converted = convert_config_to_si(fahrenheit_config)
        c_converted = convert_config_to_si(celsius_config)

        f_rate = f_converted['safety']['environments']['chamber']['rate_of_change']['temperature']
        c_rate = c_converted['safety']['environments']['chamber']['rate_of_change']['temperature']

        assert f_rate['max_per_minute'] == pytest.approx(c_rate['max_per_minute'], abs=0.01)


class TestConfigLoaderSI:
    """Verify that load_config returns SI values."""

    def test_load_config_returns_kelvin(self, fahrenheit_config):
        """load_config should return config with temperatures in Kelvin."""
        loaded = load_config(fahrenheit_config)
        target = loaded['schedules']['chamber']['phases'][0]['targets']['temperature']
        # 75°F ≈ 297.04K — definitely not 75
        assert target['ideal'] > 290
        assert target['ideal'] < 300

    def test_load_config_preserves_original_unit(self, fahrenheit_config):
        loaded = load_config(fahrenheit_config)
        assert loaded['_original_unit'] == 'F'

