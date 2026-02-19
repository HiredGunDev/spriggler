"""Cost function for environment optimization.

The cost function scores how far an environment is from its targets.
It shapes the solver's decisions by making distress expensive and
comfort cheap.

The cost curve:
    - At ideal: cost is 0
    - Between ideal and min/max: cost rises gently (quadratic)
    - Between min/max and absolute limits: cost rises steeply (quadratic, higher coefficient)
    - Beyond absolute limits: cost is effectively infinite

This means the solver naturally prioritizes environments in distress
over environments that are merely suboptimal. No priority rankings needed.
"""

import math


# Cost beyond absolute limits. High enough to dominate any combination
# of normal costs, but not so high that float arithmetic breaks.
LIMIT_BREACH_COST = 1e6

# Steepness multiplier for the region between target and absolute limit.
# Cost in this region rises this many times faster than in the target region.
CRITICAL_MULTIPLIER = 10.0


def compute_property_cost(
    value: float,
    target_min: float,
    target_max: float,
    absolute_min: float,
    absolute_max: float,
    ideal: float | None = None,
) -> float:
    """Compute the cost for a single property value.

    Args:
        value: Current measured value.
        target_min: Lower target bound (from schedule).
        target_max: Upper target bound (from schedule).
        absolute_min: Hard lower limit (from safety config).
        absolute_max: Hard upper limit (from safety config).
        ideal: Optimal value. If None, midpoint of target range is used.

    Returns:
        Non-negative cost. Zero at ideal, rising toward limits.
    """
    if ideal is None:
        ideal = (target_min + target_max) / 2.0

    # Beyond absolute limits: effectively infinite cost
    if value <= absolute_min or value >= absolute_max:
        return LIMIT_BREACH_COST

    # At ideal: zero cost
    if value == ideal:
        return 0.0

    # Between ideal and target boundary: gentle quadratic
    if target_min <= value <= target_max:
        if value < ideal:
            span = ideal - target_min
            if span == 0:
                return 0.0
            normalized = (ideal - value) / span
        else:
            span = target_max - ideal
            if span == 0:
                return 0.0
            normalized = (value - ideal) / span
        return normalized ** 2

    # Between target boundary and absolute limit: steep quadratic
    if value < target_min:
        # How far into the critical zone (0 at target_min, 1 at absolute_min)
        span = target_min - absolute_min
        if span == 0:
            return LIMIT_BREACH_COST
        normalized = (target_min - value) / span
        # Start at cost=1 (where the gentle curve ends at target_min) and rise steeply
        return 1.0 + CRITICAL_MULTIPLIER * normalized ** 2
    else:
        # value > target_max
        span = absolute_max - target_max
        if span == 0:
            return LIMIT_BREACH_COST
        normalized = (value - target_max) / span
        return 1.0 + CRITICAL_MULTIPLIER * normalized ** 2


def compute_environment_cost(
    readings: dict[str, float],
    targets: dict[str, dict],
    limits: dict[str, dict],
) -> float:
    """Compute total cost for an environment across all properties.

    Args:
        readings: Current sensor values, e.g. {"temperature": 75, "humidity": 60}
        targets: Target ranges from schedule phase, e.g.
                 {"temperature": {"min": 70, "max": 80, "ideal": 75}}
        limits: Absolute limits from safety config, e.g.
                {"temperature": {"absolute_min": 40, "absolute_max": 110}}

    Returns:
        Sum of costs across all properties that have both readings and targets.
    """
    total = 0.0

    for prop, target in targets.items():
        value = readings.get(prop)
        if value is None:
            continue

        limit = limits.get(prop, {})
        abs_min = limit.get('absolute_min')
        abs_max = limit.get('absolute_max')

        if abs_min is None or abs_max is None:
            continue

        total += compute_property_cost(
            value=value,
            target_min=target['min'],
            target_max=target['max'],
            absolute_min=abs_min,
            absolute_max=abs_max,
            ideal=target.get('ideal'),
        )

    return total

