"""Sensor freshness classification.

Every sensor reading is classified by its age relative to the
sensor's expected delivery interval.  The classification drives
control aggression:

    FRESH:  Full confidence.  Normal control decisions.
    AGING:  Reduced confidence.  Suppress aggressive actions —
            don't start high-power devices or make state changes.
    STALE:  Low confidence.  Hold current device states.
            Don't make new decisions.  Log warning.
    DEAD:   Sensor has failed.  Enter safe mode for the
            environment.  Turn off energy-adding devices.  Alert.

Thresholds are configurable multipliers of the delivery interval:
    fresh:  age < fresh_multiplier × interval  (default 1.5×)
    aging:  age < aging_multiplier × interval  (default 3.0×)
    stale:  age < dead_multiplier × interval   (default 10.0×)
    dead:   age ≥ dead_multiplier × interval

The multipliers come from config (sensor_defaults or per-sensor
overrides).
"""

from __future__ import annotations

import time
from enum import Enum

from spriggler.sensors.base import SensorReading


class Freshness(Enum):
    """Freshness classification for a sensor reading."""
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    DEAD  = "dead"

    @property
    def ok_for_control(self) -> bool:
        """Can we make normal control decisions?"""
        return self == Freshness.FRESH

    @property
    def ok_for_hold(self) -> bool:
        """Can we at least hold current state?"""
        return self in (Freshness.FRESH, Freshness.AGING, Freshness.STALE)

    @property
    def requires_safe_mode(self) -> bool:
        """Should we enter safe mode?"""
        return self == Freshness.DEAD


def classify_freshness(
    reading: SensorReading | None,
    delivery_interval: float,
    fresh_multiplier: float = 1.5,
    aging_multiplier: float = 3.0,
    dead_multiplier: float = 10.0,
    now: float | None = None,
) -> Freshness:
    """Classify the freshness of a sensor reading.

    Parameters
    ----------
    reading : SensorReading or None
        The most recent reading.  None = no reading ever received.
    delivery_interval : float
        Expected seconds between sensor deliveries (from config).
    fresh_multiplier : float
        Multiplier for fresh threshold.
    aging_multiplier : float
        Multiplier for aging threshold.
    dead_multiplier : float
        Multiplier for dead threshold.
    now : float, optional
        Current time (for testing).  Defaults to time.time().

    Returns
    -------
    Freshness
        The classification.
    """
    if reading is None:
        return Freshness.DEAD

    if now is None:
        now = time.time()

    age = now - reading.sample_time

    fresh_limit = fresh_multiplier * delivery_interval
    aging_limit = aging_multiplier * delivery_interval
    dead_limit = dead_multiplier * delivery_interval

    if age < fresh_limit:
        return Freshness.FRESH
    elif age < aging_limit:
        return Freshness.AGING
    elif age < dead_limit:
        return Freshness.STALE
    else:
        return Freshness.DEAD
