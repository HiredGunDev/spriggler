"""Schedule resolver - determines current targets from wall clock time.

Given the current time of day, finds the active schedule phase for
each environment and returns the targets and device overrides.
"""

from datetime import datetime


def _time_to_minutes(time_str: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    h, m = time_str.split(':')
    return int(h) * 60 + int(m)


def _in_phase(now_minutes: int, start_str: str, end_str: str) -> bool:
    """Check if now_minutes falls within a phase's time range."""
    start = _time_to_minutes(start_str)
    end = _time_to_minutes(end_str)

    if start == end:
        # 00:00 to 00:00 means all day
        return True
    elif end > start:
        # Normal: 06:00 to 18:00
        return start <= now_minutes < end
    else:
        # Wraps midnight: 18:00 to 06:00
        return now_minutes >= start or now_minutes < end


def resolve_phase(schedule: dict, now: datetime | None = None) -> dict:
    """Find the active phase for a single environment's schedule.

    Args:
        schedule: The schedule dict for one environment (has 'phases' list).
        now: Current time. Defaults to datetime.now().

    Returns:
        The active phase dict (with 'targets', 'devices', etc.)
    """
    if now is None:
        now = datetime.now()

    now_minutes = now.hour * 60 + now.minute

    for phase in schedule['phases']:
        if _in_phase(now_minutes, phase['start'], phase['end']):
            return phase

    # Should never happen if config validation passed (24h coverage)
    # but fall back to first phase
    return schedule['phases'][0]


def resolve_all_targets(config: dict, now: datetime | None = None) -> dict:
    """Get current targets for all environments.

    Args:
        config: Full config dict (already SI-converted).
        now: Current time. Defaults to datetime.now().

    Returns:
        {env_id: {property: {min, max, ideal}}} for all environments.
    """
    targets = {}
    for env_id, schedule in config.get('schedules', {}).items():
        phase = resolve_phase(schedule, now)
        targets[env_id] = phase.get('targets', {})
    return targets


def resolve_all_device_overrides(config: dict, now: datetime | None = None) -> dict:
    """Get current device overrides from schedule phases.

    Args:
        config: Full config dict.
        now: Current time.

    Returns:
        {device_id: state} for all schedule-forced devices.
    """
    overrides = {}
    for env_id, schedule in config.get('schedules', {}).items():
        phase = resolve_phase(schedule, now)
        overrides.update(phase.get('devices', {}))
    return overrides
