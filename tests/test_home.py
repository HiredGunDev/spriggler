"""Tests for spriggler.home - home resolution and daemon detection."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from spriggler.home import (
    resolve_home,
    resolve_config,
    check_daemon,
    HomeNotFoundError,
    ConfigNotFoundError,
    DaemonStatus,
)


# ── resolve_home ─────────────────────────────────────────────────────

class TestResolveHome:
    """Tests for home directory resolution."""

    def test_explicit_home_flag(self, tmp_path):
        """--home flag takes priority over everything."""
        result = resolve_home(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_explicit_home_nonexistent_raises(self):
        """--home pointing to nonexistent directory raises."""
        with pytest.raises(HomeNotFoundError, match="does not exist"):
            resolve_home("/nonexistent/path/to/spriggler")

    def test_env_var(self, tmp_path, monkeypatch):
        """SPRIGGLER_HOME env var is second priority."""
        monkeypatch.setenv('SPRIGGLER_HOME', str(tmp_path))
        result = resolve_home()
        assert result == tmp_path.resolve()

    def test_env_var_nonexistent_raises(self, monkeypatch):
        """SPRIGGLER_HOME pointing to nonexistent directory raises."""
        monkeypatch.setenv('SPRIGGLER_HOME', '/nonexistent/spriggler')
        with pytest.raises(HomeNotFoundError, match="does not exist"):
            resolve_home()

    def test_explicit_overrides_env(self, tmp_path, monkeypatch):
        """--home flag beats SPRIGGLER_HOME."""
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv('SPRIGGLER_HOME', str(tmp_path))
        result = resolve_home(str(other))
        assert result == other.resolve()

    def test_cwd_fallback(self, tmp_path, monkeypatch):
        """Falls back to cwd when no flag and no env var."""
        monkeypatch.delenv('SPRIGGLER_HOME', raising=False)
        monkeypatch.chdir(tmp_path)
        result = resolve_home()
        assert result == tmp_path.resolve()

    def test_resolves_to_absolute(self, tmp_path, monkeypatch):
        """Result is always an absolute path."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv('SPRIGGLER_HOME', raising=False)
        result = resolve_home()
        assert result.is_absolute()


# ── resolve_config ───────────────────────────────────────────────────

class TestResolveConfig:
    """Tests for config file resolution."""

    def test_config_found(self, tmp_path):
        """Finds config/config.json in home directory."""
        config_dir = tmp_path / 'config'
        config_dir.mkdir()
        config_file = config_dir / 'config.json'
        config_file.write_text('{}')

        result = resolve_config(tmp_path)
        assert result == config_file

    def test_config_missing_raises(self, tmp_path):
        """Raises ConfigNotFoundError when config is missing."""
        with pytest.raises(ConfigNotFoundError, match="Config not found"):
            resolve_config(tmp_path)

    def test_config_dir_exists_but_no_file(self, tmp_path):
        """Raises when config/ exists but config.json doesn't."""
        (tmp_path / 'config').mkdir()
        with pytest.raises(ConfigNotFoundError):
            resolve_config(tmp_path)

    def test_error_message_includes_paths(self, tmp_path):
        """Error message tells user what was expected and where."""
        with pytest.raises(ConfigNotFoundError) as exc_info:
            resolve_config(tmp_path)
        msg = str(exc_info.value)
        assert "config/config.json" in msg
        assert str(tmp_path) in msg


# ── check_daemon ─────────────────────────────────────────────────────

class TestCheckDaemon:
    """Tests for daemon status detection."""

    def test_no_status_file(self, tmp_path):
        """No status.json means daemon is not running."""
        result = check_daemon(tmp_path)
        assert result.running is False
        assert result.last_seen is None

    def test_recent_status_means_running(self, tmp_path):
        """Recent timestamp in status.json means daemon is alive."""
        now = datetime.now(timezone.utc)
        status = {
            'timestamp': now.isoformat(),
            'cycle': 42,
        }
        (tmp_path / 'status.json').write_text(json.dumps(status))

        result = check_daemon(tmp_path)
        assert result.running is True
        assert result.cycle == 42
        assert result.last_seen is not None

    def test_stale_status_means_not_running(self, tmp_path):
        """Old timestamp means daemon is dead."""
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        status = {
            'timestamp': old.isoformat(),
            'cycle': 100,
        }
        (tmp_path / 'status.json').write_text(json.dumps(status))

        result = check_daemon(tmp_path)
        assert result.running is False
        assert result.cycle == 100
        assert result.last_seen is not None

    def test_custom_max_age(self, tmp_path):
        """max_age_seconds controls the freshness threshold."""
        # Write a status that is 10 seconds old
        from datetime import timedelta
        ten_sec_ago = datetime.now(timezone.utc) - timedelta(seconds=10)
        status = {'timestamp': ten_sec_ago.isoformat(), 'cycle': 5}
        (tmp_path / 'status.json').write_text(json.dumps(status))

        # With 5-second max age, it's stale
        result = check_daemon(tmp_path, max_age_seconds=5.0)
        assert result.running is False

        # With 60-second max age, it's fresh
        result = check_daemon(tmp_path, max_age_seconds=60.0)
        assert result.running is True

    def test_corrupt_json(self, tmp_path):
        """Corrupt status.json is treated as daemon not running."""
        (tmp_path / 'status.json').write_text("not json {{{")
        result = check_daemon(tmp_path)
        assert result.running is False

    def test_missing_timestamp_field(self, tmp_path):
        """status.json without timestamp field means not running."""
        (tmp_path / 'status.json').write_text('{"cycle": 10}')
        result = check_daemon(tmp_path)
        assert result.running is False

    def test_invalid_timestamp_format(self, tmp_path):
        """Unparseable timestamp means not running."""
        status = {'timestamp': 'not-a-date', 'cycle': 1}
        (tmp_path / 'status.json').write_text(json.dumps(status))
        result = check_daemon(tmp_path)
        assert result.running is False

    def test_status_path_always_populated(self, tmp_path):
        """status_path is set even when file doesn't exist."""
        result = check_daemon(tmp_path)
        assert result.status_path is not None
        assert 'status.json' in result.status_path
        