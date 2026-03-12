"""Tests for sensor base classes and freshness classification."""

import time
import pytest

from spriggler.sensors.base import SensorReading, VALID_PROPERTIES
from spriggler.sensors.freshness import classify_freshness, Freshness


class TestSensorReading:
    """Test the SensorReading dataclass."""

    def test_basic_reading(self):
        now = time.time()
        r = SensorReading(
            sample_time=now,
            values={"temperature": 297.04, "absolute_humidity": 15.1},
        )
        assert r.sample_time == now
        assert r.get("temperature") == 297.04
        assert r.get("absolute_humidity") == 15.1
        assert r.get("battery") is None
        assert "temperature" in r
        assert "battery" not in r

    def test_age(self):
        r = SensorReading(
            sample_time=time.time() - 5.0,
            values={"temperature": 297.0},
        )
        assert 4.5 < r.age < 6.0

    def test_valid_properties_taxonomy(self):
        """Standard taxonomy includes our key properties."""
        assert "temperature" in VALID_PROPERTIES
        assert "absolute_humidity" in VALID_PROPERTIES
        assert "battery" in VALID_PROPERTIES
        assert "signal_strength" in VALID_PROPERTIES
        # %RH is NOT in the taxonomy — only fundamental quantities
        assert "humidity" not in VALID_PROPERTIES
        assert "relative_humidity" not in VALID_PROPERTIES


class TestFreshness:
    """Test the freshness classifier."""

    def test_fresh(self):
        """Reading within 1.5× delivery interval is FRESH."""
        now = time.time()
        r = SensorReading(sample_time=now - 10, values={"temperature": 297.0})
        f = classify_freshness(r, delivery_interval=10, now=now)
        assert f == Freshness.FRESH

    def test_aging(self):
        """Reading between 1.5× and 3× is AGING."""
        now = time.time()
        r = SensorReading(sample_time=now - 20, values={"temperature": 297.0})
        f = classify_freshness(r, delivery_interval=10, now=now)
        assert f == Freshness.AGING

    def test_stale(self):
        """Reading between 3× and 10× is STALE."""
        now = time.time()
        r = SensorReading(sample_time=now - 50, values={"temperature": 297.0})
        f = classify_freshness(r, delivery_interval=10, now=now)
        assert f == Freshness.STALE

    def test_dead(self):
        """Reading older than 10× is DEAD."""
        now = time.time()
        r = SensorReading(sample_time=now - 150, values={"temperature": 297.0})
        f = classify_freshness(r, delivery_interval=10, now=now)
        assert f == Freshness.DEAD

    def test_none_reading_is_dead(self):
        """No reading ever received = DEAD."""
        f = classify_freshness(None, delivery_interval=10)
        assert f == Freshness.DEAD

    def test_custom_multipliers(self):
        """Custom multipliers shift the boundaries."""
        now = time.time()
        # 35 seconds old, interval=10
        r = SensorReading(sample_time=now - 35, values={"temperature": 297.0})

        # Default multipliers: 1.5, 3.0, 10.0 → 35s is STALE (between 30 and 100)
        f = classify_freshness(r, delivery_interval=10, now=now)
        assert f == Freshness.STALE

        # Wider multipliers: aging up to 5× (50s) → 35s is still AGING
        f = classify_freshness(
            r, delivery_interval=10,
            aging_multiplier=5.0, dead_multiplier=15.0,
            now=now,
        )
        assert f == Freshness.AGING

    def test_freshness_boundary_exact(self):
        """Test exact boundary values."""
        now = time.time()
        interval = 10

        # Exactly at fresh boundary (15s = 1.5 × 10)
        r = SensorReading(sample_time=now - 15, values={})
        f = classify_freshness(r, delivery_interval=interval, now=now)
        # age == fresh_limit, so NOT fresh (< is strict)
        assert f == Freshness.AGING

    def test_freshness_properties(self):
        """Test the convenience properties on Freshness enum."""
        assert Freshness.FRESH.ok_for_control is True
        assert Freshness.AGING.ok_for_control is False
        assert Freshness.STALE.ok_for_control is False
        assert Freshness.DEAD.ok_for_control is False

        assert Freshness.FRESH.ok_for_hold is True
        assert Freshness.AGING.ok_for_hold is True
        assert Freshness.STALE.ok_for_hold is True
        assert Freshness.DEAD.ok_for_hold is False

        assert Freshness.FRESH.requires_safe_mode is False
        assert Freshness.AGING.requires_safe_mode is False
        assert Freshness.STALE.requires_safe_mode is False
        assert Freshness.DEAD.requires_safe_mode is True

    def test_govee_realistic_scenario(self):
        """Simulate realistic Govee H5100 delivery patterns.

        delivery_interval=10s (observed typical).
        Fresh: < 15s, Aging: < 30s, Stale: < 100s, Dead: ≥ 100s.
        """
        now = time.time()
        interval = 10

        # Just received (2s ago) — FRESH
        r = SensorReading(sample_time=now - 2, values={})
        assert classify_freshness(r, interval, now=now) == Freshness.FRESH

        # Missed one delivery (12s) — still FRESH
        r = SensorReading(sample_time=now - 12, values={})
        assert classify_freshness(r, interval, now=now) == Freshness.FRESH

        # Missed two deliveries (22s) — AGING
        r = SensorReading(sample_time=now - 22, values={})
        assert classify_freshness(r, interval, now=now) == Freshness.AGING

        # Extended gap (45s) — STALE
        r = SensorReading(sample_time=now - 45, values={})
        assert classify_freshness(r, interval, now=now) == Freshness.STALE

        # Long outage (120s) — DEAD
        r = SensorReading(sample_time=now - 120, values={})
        assert classify_freshness(r, interval, now=now) == Freshness.DEAD


class TestSensorRegistry:
    """Test the sensor driver registry."""

    def test_govee_registered(self):
        """Govee driver registers itself when imported."""
        # Import triggers registration
        from spriggler.util.discovery import discover_plugins
        discover_plugins(package="spriggler.sensors", exclude={"base", "freshness"})

        from spriggler.sensors import driver_registry
        assert driver_registry.has_driver("govee_ble")

    def test_get_unknown_returns_none(self):
        from spriggler.sensors import driver_registry
        assert driver_registry.get("nonexistent_driver") is None
