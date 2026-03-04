"""Tests for the structured logger."""

import json
import tempfile
from pathlib import Path

import pytest

from spriggler.struct_log import StructuredLogger


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / 'test.log'


@pytest.fixture
def slog(log_path):
    s = StructuredLogger(log_path, display_unit='F', console=False)
    yield s
    s.close()


def read_events(log_path: Path) -> list[dict]:
    """Read all JSON lines from a log file."""
    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


class TestStructuredLogger:

    def test_emit_writes_json_line(self, slog, log_path):
        slog.emit('test.event', cycle=1, value=42)
        events = read_events(log_path)
        assert len(events) == 1
        assert events[0]['event'] == 'test.event'
        assert events[0]['cycle'] == 1
        assert events[0]['value'] == 42

    def test_emit_includes_timestamp(self, slog, log_path):
        slog.emit('test.event')
        events = read_events(log_path)
        assert 'ts' in events[0]
        # ISO format includes T
        assert 'T' in events[0]['ts']

    def test_multiple_events(self, slog, log_path):
        slog.emit('first', a=1)
        slog.emit('second', b=2)
        slog.emit('third', c=3)
        events = read_events(log_path)
        assert len(events) == 3
        assert events[0]['event'] == 'first'
        assert events[1]['event'] == 'second'
        assert events[2]['event'] == 'third'

    def test_close_and_reopen(self, log_path):
        s1 = StructuredLogger(log_path, console=False)
        s1.emit('before', x=1)
        s1.close()

        # Reopen should append
        s2 = StructuredLogger(log_path, console=False)
        s2.emit('after', x=2)
        s2.close()

        events = read_events(log_path)
        assert len(events) == 2

    def test_display_unit_property(self, slog):
        assert slog.display_unit == 'F'
        slog.display_unit = 'C'
        assert slog.display_unit == 'C'


class TestEventFormats:
    """Test that all known event types can be emitted."""

    def test_daemon_start(self, slog, log_path):
        slog.emit('daemon.start',
                  config_name='test',
                  environments='env1, env2',
                  sensor_count=2,
                  device_count=3,
                  cycle_seconds=60,
                  display_unit='F',
                  status_path='/tmp/status.json',
                  log_path='/tmp/test.log')
        events = read_events(log_path)
        assert events[0]['event'] == 'daemon.start'
        assert events[0]['sensor_count'] == 2

    def test_sensor_reading(self, slog, log_path):
        slog.emit('sensor.reading', cycle=1,
                  sensor_id='flower',
                  environment='chamber',
                  temperature=295.37,
                  humidity=55.0,
                  battery=87,
                  signal_strength=-72)
        events = read_events(log_path)
        assert events[0]['temperature'] == 295.37
        assert events[0]['sensor_id'] == 'flower'

    def test_sensor_missed(self, slog, log_path):
        slog.emit('sensor.missed', cycle=1,
                  sensor_id='broken',
                  environment='chamber')
        events = read_events(log_path)
        assert events[0]['event'] == 'sensor.missed'

    def test_solver_result(self, slog, log_path):
        slog.emit('solver.result', cycle=1,
                  feasible=48, total=64, cost=2.0997,
                  device_states={'heater': 'on', 'fan': 'off'})
        events = read_events(log_path)
        assert events[0]['feasible'] == 48
        assert events[0]['device_states']['heater'] == 'on'

    def test_device_command(self, slog, log_path):
        slog.emit('device.command', cycle=1,
                  device_id='heater',
                  old_state='off',
                  new_state='on')
        events = read_events(log_path)
        assert events[0]['old_state'] == 'off'
        assert events[0]['new_state'] == 'on'

    def test_environment_summary(self, slog, log_path):
        slog.emit('environment.summary', cycle=1,
                  environment='flower',
                  temperature=295.37,
                  humidity=55.0,
                  target_min=291.48,
                  target_max=300.93,
                  target_ideal=297.04)
        events = read_events(log_path)
        assert events[0]['environment'] == 'flower'
        assert events[0]['target_min'] == 291.48

    def test_safety_alert(self, slog, log_path):
        slog.emit('safety.alert', cycle=1,
                  level='WARNING',
                  message='Temperature exceeds limit')
        events = read_events(log_path)
        assert events[0]['level'] == 'WARNING'

    def test_override_detected(self, slog, log_path):
        slog.emit('override.detected', cycle=1,
                  device_id='light',
                  actual_state='on',
                  commanded_state='off',
                  hold_minutes=30)
        events = read_events(log_path)
        assert events[0]['hold_minutes'] == 30

    def test_config_reload_success(self, slog, log_path):
        slog.emit('config.reload', cycle=5, success=True)
        events = read_events(log_path)
        assert events[0]['success'] is True

    def test_config_reload_failure(self, slog, log_path):
        slog.emit('config.reload', cycle=5,
                  success=False, error='bad JSON')
        events = read_events(log_path)
        assert events[0]['success'] is False
        assert events[0]['error'] == 'bad JSON'


class TestLogFileIntegrity:
    """Ensure log file is valid JSON-lines."""

    def test_each_line_is_valid_json(self, slog, log_path):
        slog.emit('a', x=1)
        slog.emit('b', x=2)
        slog.emit('c', x=3)
        slog.close()

        with open(log_path) as f:
            for i, line in enumerate(f):
                obj = json.loads(line)  # Should not raise
                assert 'ts' in obj
                assert 'event' in obj

    def test_no_trailing_comma_or_bracket(self, slog, log_path):
        """JSON-lines format: no wrapping array, no commas between lines."""
        slog.emit('a')
        slog.emit('b')
        slog.close()

        content = log_path.read_text()
        assert not content.startswith('[')
        assert not content.rstrip().endswith(',')