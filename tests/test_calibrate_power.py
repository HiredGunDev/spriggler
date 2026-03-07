"""Tests for spriggler.calibrate.power - power measurement calibration."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from spriggler.calibrate.power import (
    _find_power_monitored_devices,
    _take_power_samples,
    _summarize_samples,
)


# ── _find_power_monitored_devices ────────────────────────────────────

class TestFindPowerDevices:
    """Tests for identifying devices with power monitoring."""

    def test_kasa_plug_has_power(self):
        """Devices with kasa_plug driver have power monitoring."""
        config = {
            'devices': {
                'heater': {
                    'driver': 'kasa_plug',
                    'driver_config': {'strip': 'S', 'plug': 'H'},
                    'environment': 'chamber',
                    'circuit': 'main',
                },
            }
        }
        result = _find_power_monitored_devices(config, None)
        assert 'heater' in result

    def test_explicit_power_sensor_has_power(self):
        """Devices with power_sensor block have power monitoring."""
        config = {
            'devices': {
                'fan': {
                    'driver': 'mock',
                    'driver_config': {},
                    'environment': 'chamber',
                    'circuit': 'main',
                    'power_sensor': {
                        'driver': 'kasa_plug',
                        'driver_config': {'strip': 'S', 'plug': 'F'},
                    },
                },
            }
        }
        result = _find_power_monitored_devices(config, None)
        assert 'fan' in result

    def test_no_power_monitoring(self):
        """Devices without kasa or power_sensor are excluded."""
        config = {
            'devices': {
                'light': {
                    'driver': 'mock',
                    'driver_config': {},
                    'environment': 'chamber',
                    'circuit': 'main',
                },
            }
        }
        result = _find_power_monitored_devices(config, None)
        assert len(result) == 0

    def test_device_filter(self):
        """--device flag filters to just one device."""
        config = {
            'devices': {
                'heater': {
                    'driver': 'kasa_plug',
                    'driver_config': {},
                    'environment': 'chamber',
                    'circuit': 'main',
                },
                'fan': {
                    'driver': 'kasa_plug',
                    'driver_config': {},
                    'environment': 'chamber',
                    'circuit': 'main',
                },
            }
        }
        result = _find_power_monitored_devices(config, 'heater')
        assert 'heater' in result
        assert 'fan' not in result

    def test_device_filter_no_match(self):
        """--device with nonexistent name returns empty."""
        config = {
            'devices': {
                'heater': {
                    'driver': 'kasa_plug',
                    'driver_config': {},
                    'environment': 'chamber',
                    'circuit': 'main',
                },
            }
        }
        result = _find_power_monitored_devices(config, 'nonexistent')
        assert len(result) == 0

    def test_empty_devices(self):
        """Empty devices section returns empty."""
        config = {'devices': {}}
        result = _find_power_monitored_devices(config, None)
        assert len(result) == 0


# ── _take_power_samples ──────────────────────────────────────────────

class TestTakePowerSamples:
    """Tests for taking power readings."""

    def test_returns_correct_count(self):
        """Returns the requested number of samples."""
        driver = MagicMock()
        driver.get_power.return_value = 100.0

        readings = _take_power_samples(driver, count=3, interval=0.0)
        assert len(readings) == 3
        assert driver.get_power.call_count == 3

    def test_none_power_becomes_zero(self):
        """get_power() returning None is recorded as 0.0."""
        driver = MagicMock()
        driver.get_power.return_value = None

        readings = _take_power_samples(driver, count=2, interval=0.0)
        assert readings[0]['watts'] == 0.0

    def test_varying_readings(self):
        """Records varying power readings faithfully."""
        driver = MagicMock()
        driver.get_power.side_effect = [100.0, 105.0, 98.0]

        readings = _take_power_samples(driver, count=3, interval=0.0)
        assert readings[0]['watts'] == 100.0
        assert readings[1]['watts'] == 105.0
        assert readings[2]['watts'] == 98.0


# ── _summarize_samples ───────────────────────────────────────────────

class TestSummarizeSamples:
    """Tests for statistical summary of power samples."""

    def test_basic_stats(self):
        """Computes correct mean and stddev."""
        readings = [
            {'watts': 100.0},
            {'watts': 102.0},
            {'watts': 98.0},
            {'watts': 100.0},
            {'watts': 100.0},
        ]
        result = _summarize_samples(readings)

        assert result['watts_mean'] == 100.0
        assert result['samples'] == 5
        assert result['watts_min'] == 98.0
        assert result['watts_max'] == 102.0
        assert result['watts_stddev'] > 0

    def test_single_sample(self):
        """Single sample gives zero stddev."""
        readings = [{'watts': 150.0}]
        result = _summarize_samples(readings)

        assert result['watts_mean'] == 150.0
        assert result['watts_stddev'] == 0.0
        assert result['samples'] == 1

    def test_empty_samples(self):
        """Empty list gives zeros."""
        result = _summarize_samples([])
        assert result['watts_mean'] == 0.0
        assert result['watts_stddev'] == 0.0
        assert result['samples'] == 0

    def test_identical_readings(self):
        """Identical readings give zero stddev."""
        readings = [{'watts': 50.0}] * 5
        result = _summarize_samples(readings)

        assert result['watts_mean'] == 50.0
        assert result['watts_stddev'] == 0.0

    def test_known_stddev(self):
        """Verify stddev against known values."""
        # Values: 2, 4, 4, 4, 5, 5, 7, 9
        # Mean: 5.0, Population variance: 4.0
        # Sample variance (n-1): 4.571..., Sample stddev: 2.138...
        readings = [{'watts': w} for w in [2, 4, 4, 4, 5, 5, 7, 9]]
        result = _summarize_samples(readings)

        assert abs(result['watts_mean'] - 5.0) < 0.01
        expected_stddev = math.sqrt(32 / 7)  # sample stddev
        assert abs(result['watts_stddev'] - expected_stddev) < 0.01

    def test_raw_values_preserved(self):
        """Raw readings are included in output."""
        readings = [{'watts': 10.0}, {'watts': 20.0}]
        result = _summarize_samples(readings)
        assert result['raw'] == [10.0, 20.0]


# ── Integration: daemon check ────────────────────────────────────────

class TestDaemonGuard:
    """Tests that calibration refuses to run when daemon is active."""

    def test_refuses_when_daemon_running(self, tmp_path):
        """Calibration exits with error when daemon is alive."""
        # Write a fresh status.json
        status = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'cycle': 10,
        }
        (tmp_path / 'status.json').write_text(json.dumps(status))

        from spriggler.home import check_daemon
        result = check_daemon(tmp_path)
        assert result.running is True

    def test_proceeds_when_daemon_stopped(self, tmp_path):
        """Calibration proceeds when no daemon is running."""
        from spriggler.home import check_daemon
        result = check_daemon(tmp_path)
        assert result.running is False

    def test_proceeds_with_force_flag(self, tmp_path):
        """--force skips the daemon check."""
        # This is a behavioral test — the actual skip is in
        # run_power_calibration which checks args.force
        status = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'cycle': 10,
        }
        (tmp_path / 'status.json').write_text(json.dumps(status))

        from spriggler.home import check_daemon
        result = check_daemon(tmp_path)
        assert result.running is True
        # With --force, we'd skip this check — tested at integration level