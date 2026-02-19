"""Solver - finds the optimal device state combination.

The solver enumerates all feasible combinations of device states,
uses a physics model to predict the outcome of each combination,
scores each prediction with the cost function, and returns the
combination with the lowest total cost across all environments.

The solver is stateless. It takes a snapshot of current conditions
and returns a recommendation. It has no memory of previous decisions.
The main loop calls it on every control cycle.

The solver respects:
    - Circuit amperage limits (hard constraint, combinations that
      exceed any circuit are discarded)
    - Safety monitor vetoes (locked-out devices are fixed to safe state)
    - Schedule device overrides (lights follow the schedule, not the solver)
"""

import itertools
from dataclasses import dataclass

from spriggler.solver.cost import compute_environment_cost


@dataclass
class SolverResult:
    """The solver's recommendation for one control cycle."""
    device_states: dict[str, str]    # device_id -> state name
    total_cost: float
    feasible_count: int              # how many combinations were evaluated
    total_count: int                 # how many combinations exist (before filtering)


class Solver:
    """Enumerates feasible device states and picks the lowest-cost combination."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._circuits = config['circuits']
        self._devices = config['devices']
        self._safety = config['safety']
        self._schedules = config['schedules']

    def solve(
        self,
        current_readings: dict[str, dict[str, float]],
        device_states_available: dict[str, list[str]],
        locked_out_devices: set[str],
        schedule_overrides: dict[str, str],
        predict_fn,
        current_phase_targets: dict[str, dict[str, dict]],
        device_amps: dict[str, dict[str, float]] | None = None,
    ) -> SolverResult:
        """Find the optimal device state combination.

        Args:
            current_readings: {env_id: {property: value}} for all environments.
            device_states_available: {device_id: [state_names]} from drivers.
            locked_out_devices: Set of device IDs the safety monitor has locked out.
            schedule_overrides: {device_id: state} forced by the current schedule phase.
            predict_fn: Callable(current_readings, proposed_states) -> predicted_readings.
                        Takes current readings and a dict of {device_id: state},
                        returns {env_id: {property: predicted_value}}.
            current_phase_targets: {env_id: {property: {min, max, ideal}}}
                                   from the active schedule phase.
            device_amps: {device_id: {state: amps}} estimated amperage per state.
                         If None, circuit constraints are not checked.

        Returns:
            SolverResult with the winning device states and metadata.
        """
        # Build the list of devices and their candidate states
        device_ids = []
        state_lists = []

        for device_id, available in device_states_available.items():
            device_ids.append(device_id)

            if device_id in schedule_overrides:
                # Forced by schedule — only one option
                state_lists.append([schedule_overrides[device_id]])
            elif device_id in locked_out_devices:
                # Locked out — force to safe state
                safe = self._get_safe_state(device_id)
                state_lists.append([safe])
            else:
                state_lists.append(available)

        # Total combinations before filtering
        total_count = 1
        for states in state_lists:
            total_count *= len(states)

        # Enumerate and evaluate
        best_result = None
        best_cost = float('inf')
        feasible_count = 0

        for combo in itertools.product(*state_lists):
            proposed = dict(zip(device_ids, combo))

            # Check circuit constraints
            if device_amps and not self._check_circuits(proposed, device_amps):
                continue

            feasible_count += 1

            # Predict outcome
            predicted = predict_fn(current_readings, proposed)

            # Score
            total_cost = self._score(predicted, current_phase_targets)

            if total_cost < best_cost:
                best_cost = total_cost
                best_result = proposed

        # If nothing was feasible (shouldn't happen — all-off should always work)
        if best_result is None:
            best_result = {did: 'off' for did in device_ids}
            best_cost = float('inf')

        return SolverResult(
            device_states=best_result,
            total_cost=best_cost,
            feasible_count=feasible_count,
            total_count=total_count,
        )

    def _get_safe_state(self, device_id: str) -> str:
        """Get the safe state for a device from the safety config."""
        dev_safety = self._safety.get('devices', {}).get(device_id, {})
        return dev_safety.get('safe_state', 'off')

    def _check_circuits(
        self,
        proposed: dict[str, str],
        device_amps: dict[str, dict[str, float]],
    ) -> bool:
        """Check if a proposed combination violates any circuit limit.

        Returns True if the combination is feasible, False if it violates.
        """
        # Accumulate amps per circuit
        circuit_load: dict[str, float] = {}

        for device_id, state in proposed.items():
            device_cfg = self._devices.get(device_id, {})
            circuit_id = device_cfg.get('circuit')
            if not circuit_id:
                continue

            amps = device_amps.get(device_id, {}).get(state, 0.0)
            circuit_load[circuit_id] = circuit_load.get(circuit_id, 0.0) + amps

        # Check against limits
        for circuit_id, load in circuit_load.items():
            circuit_cfg = self._circuits.get(circuit_id, {})
            max_amps = circuit_cfg.get('max_amps', float('inf'))
            if load > max_amps:
                return False

        return True

    def _score(
        self,
        predicted: dict[str, dict[str, float]],
        targets: dict[str, dict[str, dict]],
    ) -> float:
        """Score predicted readings against targets across all environments."""
        total = 0.0

        env_safety = self._safety.get('environments', {})

        for env_id, env_targets in targets.items():
            readings = predicted.get(env_id, {})
            limits = env_safety.get(env_id, {}).get('limits', {})
            total += compute_environment_cost(readings, env_targets, limits)

        return total

