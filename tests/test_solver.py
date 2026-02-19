"""Tests for the cost function and solver.

The cost function is tested with exact values to verify the curve shape.
The solver is tested with mock physics models that return predictable
results, verifying enumeration, constraint filtering, and selection.
"""

import pytest
import math

from spriggler.solver.cost import (
    compute_property_cost,
    compute_environment_cost,
    LIMIT_BREACH_COST,
    CRITICAL_MULTIPLIER,
)
from spriggler.solver.solver import Solver, SolverResult


# ══════════════════════════════════════════════════════════════════════════════
# Cost function tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPropertyCost:
    """Test the cost curve for a single property."""

    # Standard test range: target 70-80, absolute 40-110, ideal 75
    TMIN = 70
    TMAX = 80
    AMIN = 40
    AMAX = 110
    IDEAL = 75

    def _cost(self, value, ideal=None):
        return compute_property_cost(
            value, self.TMIN, self.TMAX, self.AMIN, self.AMAX,
            ideal=ideal or self.IDEAL,
        )

    def test_cost_at_ideal_is_zero(self):
        assert self._cost(75) == 0.0

    def test_cost_at_target_min(self):
        """At target_min, cost should be 1.0 (end of gentle curve)."""
        assert self._cost(70) == pytest.approx(1.0)

    def test_cost_at_target_max(self):
        """At target_max, cost should be 1.0 (end of gentle curve)."""
        assert self._cost(80) == pytest.approx(1.0)

    def test_cost_between_ideal_and_target_min(self):
        """Midpoint of gentle curve: normalized=0.5, cost=0.25."""
        assert self._cost(72.5) == pytest.approx(0.25)

    def test_cost_between_ideal_and_target_max(self):
        """Midpoint of gentle curve on high side."""
        assert self._cost(77.5) == pytest.approx(0.25)

    def test_cost_increases_below_target_min(self):
        """Below target_min, cost should be > 1.0 (critical zone)."""
        assert self._cost(65) > 1.0

    def test_cost_increases_above_target_max(self):
        """Above target_max, cost should be > 1.0 (critical zone)."""
        assert self._cost(90) > 1.0

    def test_cost_in_critical_zone_steeper_than_target_zone(self):
        """The same fractional distance in the critical zone costs more."""
        # 50% into target zone (low side): value=72.5, cost=0.25
        target_cost = self._cost(72.5)
        # 50% into critical zone (low side): value=55
        critical_cost = self._cost(55)
        assert critical_cost > target_cost * CRITICAL_MULTIPLIER

    def test_cost_at_absolute_min_is_breach(self):
        assert self._cost(40) == LIMIT_BREACH_COST

    def test_cost_at_absolute_max_is_breach(self):
        assert self._cost(110) == LIMIT_BREACH_COST

    def test_cost_below_absolute_min_is_breach(self):
        assert self._cost(30) == LIMIT_BREACH_COST

    def test_cost_above_absolute_max_is_breach(self):
        assert self._cost(120) == LIMIT_BREACH_COST

    def test_cost_is_symmetric_around_ideal(self):
        """Equal distance above and below ideal should give equal cost."""
        cost_low = self._cost(73)   # 2 below ideal
        cost_high = self._cost(77)  # 2 above ideal
        assert cost_low == pytest.approx(cost_high)

    def test_cost_monotonically_increases_below_ideal(self):
        """Cost should increase as value moves further below ideal."""
        costs = [self._cost(v) for v in [74, 72, 70, 60, 50]]
        for i in range(len(costs) - 1):
            assert costs[i] < costs[i + 1]

    def test_cost_monotonically_increases_above_ideal(self):
        """Cost should increase as value moves further above ideal."""
        costs = [self._cost(v) for v in [76, 78, 80, 90, 100]]
        for i in range(len(costs) - 1):
            assert costs[i] < costs[i + 1]

    def test_default_ideal_is_midpoint(self):
        """When ideal is not specified, midpoint of target range is used."""
        cost = compute_property_cost(75, 70, 80, 40, 110, ideal=None)
        assert cost == 0.0

    def test_asymmetric_ideal(self):
        """Ideal closer to min than max should make low-side cheaper."""
        # Ideal at 72, range 70-80. 2 degrees below ideal vs 2 degrees above.
        cost_low = compute_property_cost(70, 70, 80, 40, 110, ideal=72)
        cost_high = compute_property_cost(74, 70, 80, 40, 110, ideal=72)
        # low is at target_min (cost=1.0), high is 2/8 of the way to target_max
        assert cost_low > cost_high


class TestEnvironmentCost:
    """Test cost computation across multiple properties."""

    def test_all_at_ideal_is_zero(self):
        readings = {"temperature": 75, "humidity": 60}
        targets = {
            "temperature": {"min": 70, "max": 80, "ideal": 75},
            "humidity": {"min": 50, "max": 70, "ideal": 60},
        }
        limits = {
            "temperature": {"absolute_min": 40, "absolute_max": 110},
            "humidity": {"absolute_min": 10, "absolute_max": 95},
        }
        assert compute_environment_cost(readings, targets, limits) == 0.0

    def test_one_property_off_ideal(self):
        """Cost should be nonzero when one property is off ideal."""
        readings = {"temperature": 72, "humidity": 60}
        targets = {
            "temperature": {"min": 70, "max": 80, "ideal": 75},
            "humidity": {"min": 50, "max": 70, "ideal": 60},
        }
        limits = {
            "temperature": {"absolute_min": 40, "absolute_max": 110},
            "humidity": {"absolute_min": 10, "absolute_max": 95},
        }
        cost = compute_environment_cost(readings, targets, limits)
        assert cost > 0.0

    def test_costs_are_additive(self):
        """Total cost should be sum of individual property costs."""
        targets = {
            "temperature": {"min": 70, "max": 80, "ideal": 75},
            "humidity": {"min": 50, "max": 70, "ideal": 60},
        }
        limits = {
            "temperature": {"absolute_min": 40, "absolute_max": 110},
            "humidity": {"absolute_min": 10, "absolute_max": 95},
        }
        temp_cost = compute_property_cost(72, 70, 80, 40, 110, ideal=75)
        hum_cost = compute_property_cost(55, 50, 70, 10, 95, ideal=60)
        combined = compute_environment_cost(
            {"temperature": 72, "humidity": 55}, targets, limits
        )
        assert combined == pytest.approx(temp_cost + hum_cost)

    def test_missing_reading_ignored(self):
        """Properties without readings contribute zero cost."""
        targets = {
            "temperature": {"min": 70, "max": 80, "ideal": 75},
            "humidity": {"min": 50, "max": 70, "ideal": 60},
        }
        limits = {
            "temperature": {"absolute_min": 40, "absolute_max": 110},
            "humidity": {"absolute_min": 10, "absolute_max": 95},
        }
        # Only temperature reading, no humidity
        cost = compute_environment_cost(
            {"temperature": 75}, targets, limits
        )
        assert cost == 0.0

    def test_missing_limits_ignored(self):
        """Properties without limits contribute zero cost."""
        targets = {
            "temperature": {"min": 70, "max": 80, "ideal": 75},
        }
        limits = {}  # No limits defined
        cost = compute_environment_cost(
            {"temperature": 72}, targets, limits
        )
        assert cost == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Solver tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def solver_config():
    """Config for solver tests — two environments, shared circuit."""
    return {
        "version": "0.3",
        "name": "Test",
        "units": {"temperature": "F"},
        "environments": {
            "chamber": {"description": "Test chamber"},
            "pod": {"description": "Seedling pod"},
        },
        "sensors": {
            "chamber_sensor": {
                "driver": "mock", "environment": "chamber",
                "properties": ["temperature"], "driver_config": {}
            },
            "pod_sensor": {
                "driver": "mock", "environment": "pod",
                "properties": ["temperature"], "driver_config": {}
            },
            "ambient_sensor": {
                "driver": "mock", "environment": "ambient",
                "properties": ["temperature"], "driver_config": {}
            },
        },
        "devices": {
            "chamber_heater": {
                "driver": "mock", "environment": "chamber",
                "circuit": "main", "role": "heater", "driver_config": {}
            },
            "pod_heater": {
                "driver": "mock", "environment": "pod",
                "circuit": "main", "role": "heater", "driver_config": {}
            },
            "chamber_exhaust": {
                "driver": "mock", "environment": "chamber",
                "circuit": "main", "role": "exhaust", "driver_config": {}
            },
        },
        "circuits": {
            "main": {"max_amps": 20, "voltage": 120}
        },
        "schedules": {
            "chamber": {
                "phases": [{
                    "name": "always", "start": "00:00", "end": "00:00",
                    "targets": {
                        "temperature": {"min": 70, "max": 80, "ideal": 75}
                    }
                }]
            },
            "pod": {
                "phases": [{
                    "name": "always", "start": "00:00", "end": "00:00",
                    "targets": {
                        "temperature": {"min": 75, "max": 82, "ideal": 78}
                    }
                }]
            },
        },
        "safety": {
            "environments": {
                "chamber": {
                    "limits": {
                        "temperature": {"absolute_min": 40, "absolute_max": 110}
                    }
                },
                "pod": {
                    "limits": {
                        "temperature": {"absolute_min": 55, "absolute_max": 95}
                    }
                },
            },
            "devices": {
                "chamber_heater": {"safe_state": "off"},
                "pod_heater": {"safe_state": "off"},
                "chamber_exhaust": {"safe_state": "on"},
            },
            "sensor_stale_after_missed": 3,
            "safety_loop_interval_seconds": 15,
        }
    }


@pytest.fixture
def solver(solver_config):
    return Solver(solver_config)


def make_predict_fn(
    effects: dict[str, dict[str, dict[str, float]]],
    drift: dict[str, dict[str, float]] | None = None,
):
    """Create a mock predict_fn from device effects and environmental drift.

    The predict_fn models two forces:
    1. Drift: how each environment moves without intervention (envelope loss
       toward ambient). Applied to every prediction regardless of device states.
    2. Device effects: deltas applied on top of drift when devices are active.

    Args:
        effects: {device_id: {state: {env_id: {property: delta}}}}
                 e.g. {"chamber_heater": {"on": {"chamber": {"temperature": +10}}}}
        drift: {env_id: {property: delta}} baseline change per cycle with no devices.
               e.g. {"chamber": {"temperature": -3}} means chamber loses 3°F/cycle.

    Returns:
        A predict_fn(current_readings, proposed_states) -> predicted_readings.
    """
    if drift is None:
        drift = {}

    def predict(current_readings, proposed_states):
        predicted = {}
        for env_id, readings in current_readings.items():
            predicted[env_id] = dict(readings)
            # Apply drift (envelope loss toward ambient)
            for prop, delta in drift.get(env_id, {}).items():
                predicted[env_id][prop] = predicted[env_id].get(prop, 0) + delta

        for device_id, state in proposed_states.items():
            device_effects = effects.get(device_id, {})
            state_effects = device_effects.get(state, {})
            for env_id, props in state_effects.items():
                if env_id not in predicted:
                    predicted[env_id] = {}
                for prop, delta in props.items():
                    current = predicted[env_id].get(prop, 0)
                    predicted[env_id][prop] = current + delta

        return predicted
    return predict


# ── Basic solver behavior ────────────────────────────────────────────────────

class TestSolverBasic:

    def test_solver_returns_result(self, solver):
        """Solver should return a SolverResult."""
        result = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=lambda readings, states: readings,  # identity
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        assert isinstance(result, SolverResult)
        assert isinstance(result.device_states, dict)
        assert result.total_cost >= 0

    def test_mild_day_at_ideal_everything_off(self, solver):
        """On a mild day with no drift, solver should leave devices off."""
        # Ambient is close to target — no envelope loss
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 5}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 5}},
                    "off": {},
                },
                "chamber_exhaust": {"on": {}, "off": {}},
            },
            drift={},  # No drift — mild day, ambient ≈ target
        )
        result = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        assert result.device_states["chamber_heater"] == "off"
        assert result.device_states["pod_heater"] == "off"
        assert result.total_cost == pytest.approx(0.0)

    def test_cold_day_at_ideal_heater_maintains(self, solver):
        """On a cold day, solver should run heater to counteract envelope loss."""
        # Chamber drifts -5°F/cycle, heater adds +5°F — net zero, stays at ideal
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 5}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 5}},
                    "off": {},
                },
                "chamber_exhaust": {"on": {}, "off": {}},
            },
            drift={
                "chamber": {"temperature": -5},
                "pod": {"temperature": -5},
            },
        )
        result = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        # Chamber: heater on → 75 - 5 + 5 = 75 (ideal). Heater off → 75 - 5 = 70.
        # Solver should run the heater to stay at ideal.
        assert result.device_states["chamber_heater"] == "on"

    def test_cold_chamber_heater_on(self, solver):
        """When chamber is cold, solver should turn on the heater."""
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 10}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 10}},
                    "off": {},
                },
                "chamber_exhaust": {"on": {}, "off": {}},
            },
            drift={
                "chamber": {"temperature": -3},
                "pod": {"temperature": -3},
            },
        )
        result = solver.solve(
            current_readings={"chamber": {"temperature": 60}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        # Chamber at 60, drift to 57 without heat, or 67 with heat. On is better.
        assert result.device_states["chamber_heater"] == "on"

    def test_hot_chamber_heater_off(self, solver):
        """When chamber is too hot, solver should not run heater."""
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 5}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 5}},
                    "off": {},
                },
                "chamber_exhaust": {
                    "on": {"chamber": {"temperature": -5}},
                    "off": {},
                },
            },
            drift={
                "chamber": {"temperature": 2},   # Hot day, drifting up
                "pod": {"temperature": -1},
            },
        )
        result = solver.solve(
            current_readings={"chamber": {"temperature": 85}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        assert result.device_states["chamber_heater"] == "off"
        # Exhaust cools: 85 + 2 - 5 = 82 vs 85 + 2 = 87 without
        assert result.device_states["chamber_exhaust"] == "on"


# ── Circuit constraints ──────────────────────────────────────────────────────

class TestCircuitConstraints:

    def test_circuit_limit_prevents_both_heaters(self, solver):
        """When both heaters exceed circuit, solver picks the more needed one."""
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 15}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 15}},
                    "off": {},
                },
                "chamber_exhaust": {"on": {}, "off": {}},
            },
            drift={
                "chamber": {"temperature": -3},
                "pod": {"temperature": -3},
            },
        )

        device_amps = {
            "chamber_heater": {"off": 0, "on": 12.5},
            "pod_heater": {"off": 0, "on": 12.5},
            "chamber_exhaust": {"off": 0, "on": 0.5},
        }

        result = solver.solve(
            current_readings={"chamber": {"temperature": 60}, "pod": {"temperature": 60}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
            device_amps=device_amps,
        )
        # Both can't be on (12.5 + 12.5 = 25 > 20 amp limit)
        assert not (
            result.device_states["chamber_heater"] == "on" and
            result.device_states["pod_heater"] == "on"
        )
        # But one should be on
        assert (
            result.device_states["chamber_heater"] == "on" or
            result.device_states["pod_heater"] == "on"
        )

    def test_feasible_count_less_than_total(self, solver):
        """Circuit constraints should reduce feasible combinations."""
        predict = make_predict_fn(
            effects={
                "chamber_heater": {"on": {"chamber": {"temperature": 5}}, "off": {}},
                "pod_heater": {"on": {"pod": {"temperature": 5}}, "off": {}},
                "chamber_exhaust": {"on": {}, "off": {}},
            },
        )
        device_amps = {
            "chamber_heater": {"off": 0, "on": 12.5},
            "pod_heater": {"off": 0, "on": 12.5},
            "chamber_exhaust": {"off": 0, "on": 0.5},
        }
        result = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
            device_amps=device_amps,
        )
        assert result.feasible_count < result.total_count

    def test_no_amps_data_skips_circuit_check(self, solver):
        """When device_amps is None, all combinations are feasible."""
        predict = lambda r, s: r
        result = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
            device_amps=None,
        )
        assert result.feasible_count == result.total_count


# ── Schedule overrides ───────────────────────────────────────────────────────

class TestScheduleOverrides:

    def test_schedule_override_forces_state(self, solver):
        """A schedule override should force the device to the specified state."""
        predict = lambda r, s: r
        result = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={"chamber_exhaust": "on"},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        assert result.device_states["chamber_exhaust"] == "on"

    def test_schedule_override_reduces_combinations(self, solver):
        """Schedule overrides should reduce the search space."""
        predict = lambda r, s: r
        result_without = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        result_with = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={"chamber_exhaust": "on"},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        assert result_with.total_count < result_without.total_count


# ── Locked out devices ───────────────────────────────────────────────────────

class TestLockedOutDevices:

    def test_locked_out_device_forced_to_safe_state(self, solver):
        """Locked out device should be forced to its safe state."""
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 10}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 10}},
                    "off": {},
                },
                "chamber_exhaust": {"on": {}, "off": {}},
            },
            drift={"chamber": {"temperature": -3}, "pod": {"temperature": -3}},
        )
        result = solver.solve(
            current_readings={"chamber": {"temperature": 50}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices={"chamber_heater"},
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        assert result.device_states["chamber_heater"] == "off"


# ── Graduated devices ────────────────────────────────────────────────────────

class TestGraduatedDevices:

    def test_graduated_picks_best_level(self, solver):
        """Solver should pick the graduated level closest to ideal."""
        # Chamber at 70 with -2 drift. Need to get close to 75.
        # off: 70-2=68, low: 70-2+3=71, mid: 70-2+7=75, high: 70-2+12=80
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "off":  {},
                    "low":  {"chamber": {"temperature": 3}},
                    "mid":  {"chamber": {"temperature": 7}},
                    "high": {"chamber": {"temperature": 12}},
                },
                "pod_heater": {"off": {}, "on": {"pod": {"temperature": 5}}},
                "chamber_exhaust": {"off": {}, "on": {}},
            },
            drift={
                "chamber": {"temperature": -2},
                "pod": {"temperature": -2},
            },
        )
        result = solver.solve(
            current_readings={"chamber": {"temperature": 70}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "low", "mid", "high"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        # mid lands at exactly 75 (ideal). Best choice.
        assert result.device_states["chamber_heater"] == "mid"

    def test_graduated_enumeration_count(self, solver):
        """Graduated device should expand the search space correctly."""
        predict = lambda r, s: r
        result = solver.solve(
            current_readings={"chamber": {"temperature": 75}, "pod": {"temperature": 78}},
            device_states_available={
                "chamber_heater": ["off", "low", "mid", "high"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
        )
        # 4 * 2 * 2 = 16
        assert result.total_count == 16


# ── Multi-environment triage ─────────────────────────────────────────────────

class TestMultiEnvironmentTriage:

    def test_prioritizes_environment_in_distress(self, solver):
        """When only one heater can run, solver should pick the more distressed env."""
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 10}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 10}},
                    "off": {},
                },
                "chamber_exhaust": {"on": {}, "off": {}},
            },
            drift={
                "chamber": {"temperature": -3},
                "pod": {"temperature": -3},
            },
        )
        device_amps = {
            "chamber_heater": {"off": 0, "on": 12.5},
            "pod_heater": {"off": 0, "on": 12.5},
            "chamber_exhaust": {"off": 0, "on": 0.5},
        }

        # Pod at 60 → without heat drifts to 57 (close to absolute_min 55)
        # Chamber at 65 → without heat drifts to 62 (well above absolute_min 40)
        result = solver.solve(
            current_readings={"chamber": {"temperature": 65}, "pod": {"temperature": 60}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
            device_amps=device_amps,
        )
        assert result.device_states["pod_heater"] == "on"
        assert result.device_states["chamber_heater"] == "off"

    def test_both_heaters_when_circuit_allows(self, solver):
        """When circuit has capacity, both heaters should run if both needed."""
        predict = make_predict_fn(
            effects={
                "chamber_heater": {
                    "on": {"chamber": {"temperature": 10}},
                    "off": {},
                },
                "pod_heater": {
                    "on": {"pod": {"temperature": 10}},
                    "off": {},
                },
                "chamber_exhaust": {"on": {}, "off": {}},
            },
            drift={
                "chamber": {"temperature": -3},
                "pod": {"temperature": -3},
            },
        )
        device_amps = {
            "chamber_heater": {"off": 0, "on": 8},
            "pod_heater": {"off": 0, "on": 8},
            "chamber_exhaust": {"off": 0, "on": 0.5},
        }

        result = solver.solve(
            current_readings={"chamber": {"temperature": 60}, "pod": {"temperature": 60}},
            device_states_available={
                "chamber_heater": ["off", "on"],
                "pod_heater": ["off", "on"],
                "chamber_exhaust": ["off", "on"],
            },
            locked_out_devices=set(),
            schedule_overrides={},
            predict_fn=predict,
            current_phase_targets={
                "chamber": {"temperature": {"min": 70, "max": 80, "ideal": 75}},
                "pod": {"temperature": {"min": 75, "max": 82, "ideal": 78}},
            },
            device_amps=device_amps,
        )
        # 8 + 8 + 0.5 = 16.5, under 20 amp limit — both should run
        assert result.device_states["chamber_heater"] == "on"
        assert result.device_states["pod_heater"] == "on"

