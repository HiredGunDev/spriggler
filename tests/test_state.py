"""Tests for state directory resolution."""

import os
from pathlib import Path

import pytest

from spriggler.state import resolve_state_dir, ensure_state_dir, DEFAULT_STATE_DIR


class TestResolveStateDir:

    def test_default_is_home_dotspriggler(self):
        result = resolve_state_dir(None)
        assert result == DEFAULT_STATE_DIR
        assert result.name == '.spriggler'

    def test_override_takes_priority(self, tmp_path):
        result = resolve_state_dir(str(tmp_path / 'custom'))
        assert result == tmp_path / 'custom'

    def test_env_var_used_when_no_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv('SPRIGGLER_STATE_DIR', str(tmp_path / 'env'))
        result = resolve_state_dir(None)
        assert result == tmp_path / 'env'

    def test_override_beats_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv('SPRIGGLER_STATE_DIR', str(tmp_path / 'env'))
        result = resolve_state_dir(str(tmp_path / 'flag'))
        assert result == tmp_path / 'flag'

    def test_result_is_absolute(self):
        result = resolve_state_dir('relative/path')
        assert result.is_absolute()


class TestEnsureStateDir:

    def test_creates_directory(self, tmp_path):
        target = tmp_path / 'new_dir'
        assert not target.exists()
        result = ensure_state_dir(target)
        assert target.is_dir()
        assert result == target

    def test_existing_directory_ok(self, tmp_path):
        target = tmp_path / 'existing'
        target.mkdir()
        result = ensure_state_dir(target)
        assert target.is_dir()
        assert result == target

    def test_creates_parents(self, tmp_path):
        target = tmp_path / 'a' / 'b' / 'c'
        ensure_state_dir(target)
        assert target.is_dir()