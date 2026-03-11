"""Trajectory Planner — forward-simulating solver for Spriggler v0.4.

Instead of scoring a single predicted state one cycle ahead, the
planner simulates forward through a physics-derived horizon and
scores the entire trajectory.  This lets it see the consequences
of actions that unfold over minutes — like a heater's 5°F coast
overshoot 90 seconds after shutoff.

Architecture:
    1. For each candidate first-action (device combination):
       a. Simulate forward N steps using calibrated rates,
          coast profiles, and envelope conductance.
       b. At each simulated step after the first, pick the
          greedy best action (lowest next-step cost) to
          continue the rollout.
       c. Score the full trajectory (discounted integral of
          per-step cost).
    2. Pick the first-action whose trajectory has the lowest
       total cost.
    3. Execute only that first action.  Next cycle, re-plan
       with fresh sensor data.

The planning horizon is derived from calibration: it's at least as
long as the longest coast duration, plus a fraction of the envelope
time constant.
"""

import itertools
import math
from dataclasses import dataclass, field

from spriggler.solver.cost import (
    compute_environment_cost, compute_property_cost,
)


# Map device role to its primary controlled property.
# The planner only evaluates a device's effect on its primary
# property when deciding whether to turn it on.  Side effects
# (heater drying humidity, humidifier cooling temperature) are
# predicted for trajectory accuracy but masked from scoring.
_ROLE_PRIMARY = {
    'heater': 'temperature',
    'cooler': 'temperature',
    'humidifier': 'humidity',
    'dehumidifier': 'humidity',
    'exhaust': 'temperature',
    'intake': 'temperature',
    'circulation': 'temperature',
    'light': 'temperature',
}


@dataclass
class TrajectoryStep:
    """One simulated step in a trajectory."""
    step: int
    elapsed_s: float
    readings: dict[str, dict[str, float]]  # {env_id: {prop: value}}
    action: dict[str, str]                 # {device_id: state}
    cost: float


@dataclass
class TrajectoryResult:
    """Result of evaluating one candidate first-action."""
    first_action: dict[str, str]
    trajectory_cost: float
    steps: list[TrajectoryStep] = field(default_factory=list)


@dataclass
class PlannerResult:
    """The planner's recommendation for one control cycle."""
    device_states: dict[str, str]    # device_id -> state name
    total_cost: float
    horizon_steps: int
    horizon_seconds: float
    feasible_count: int
    total_count: int
    trajectory: list[TrajectoryStep] = field(default_factory=list)


class TrajectoryPlanner:
    """Forward-simulating solver using calibrated physics model."""

    # Discount factor per step: near-term deviations matter more
    DISCOUNT = 0.97

    # Minimum planning horizon in steps (even if coast is short)
    MIN_HORIZON_STEPS = 8   # ~2 minutes at 15s cycles

    # Maximum planning horizon in steps (computational bound)
    MAX_HORIZON_STEPS = 60  # ~15 minutes at 15s cycles

    # Energy penalty per watt per step.  Must be small enough to
    # never override a genuine physics-based decision, but large
    # enough to break ties between devices that achieve similar
    # outcomes.  At 0.00001/W, a 600W heater adds 0.006 cost per
    # step — negligible compared to typical property costs (0.5-5.0),
    # but enough to prefer a 2W fan when outcomes are otherwise equal.
    ENERGY_PENALTY = 0.00001

    def __init__(self, config: dict) -> None:
        self._config = config
        self._circuits = config['circuits']
        self._devices = config['devices']
        self._safety = config['safety']
        self._power_watts: dict[str, dict[str, float]] = {}

    def plan(
            self,
            current_readings: dict[str, dict[str, float]],
            device_states_available: dict[str, list[str]],
            locked_out_devices: set[str],
            schedule_overrides: dict[str, str],
            current_phase_targets: dict[str, dict[str, dict]],
            device_amps: dict[str, dict[str, float]] | None,
            ambient: dict[str, float],
            calibration: dict,
            device_env_map: dict[str, str],
            current_device_states: dict[str, str],
            coast_profiles: dict[str, dict[str, list[dict]]],
            cycle_seconds: float = 15.0,
            power_watts: dict[str, dict[str, float]] | None = None,
    ) -> PlannerResult:
        """Find the best first-action by simulating trajectories."""
        dt = cycle_seconds
        targets = current_phase_targets
        env_safety = self._safety.get('environments', {})
        self._power_watts = power_watts or {}

        # Compute planning horizon from calibration data
        horizon = self._compute_horizon(
            calibration, coast_profiles, dt)

        # Build candidate first-actions
        device_ids, state_lists = self._build_candidates(
            device_states_available, locked_out_devices,
            schedule_overrides, current_readings, targets)

        total_count = 1
        for sl in state_lists:
            total_count *= len(sl)

        # Evaluate each candidate
        best: TrajectoryResult | None = None
        feasible_count = 0

        for combo in itertools.product(*state_lists):
            first_action = dict(zip(device_ids, combo))

            if device_amps and not self._check_circuits(
                    first_action, device_amps):
                continue

            feasible_count += 1

            result = self._simulate_trajectory(
                first_action=first_action,
                current_readings=current_readings,
                ambient=ambient,
                calibration=calibration,
                device_env_map=device_env_map,
                current_device_states=current_device_states,
                coast_profiles=coast_profiles,
                targets=targets,
                env_safety=env_safety,
                device_ids=device_ids,
                state_lists=state_lists,
                schedule_overrides=schedule_overrides,
                locked_out_devices=locked_out_devices,
                device_amps=device_amps,
                horizon=horizon,
                dt=dt,
            )

            if best is None or result.trajectory_cost < best.trajectory_cost:
                best = result

        if best is None:
            best = TrajectoryResult(
                first_action={did: 'off' for did in device_ids},
                trajectory_cost=float('inf'))

        return PlannerResult(
            device_states=best.first_action,
            total_cost=best.trajectory_cost,
            horizon_steps=horizon,
            horizon_seconds=horizon * dt,
            feasible_count=feasible_count,
            total_count=total_count,
            trajectory=best.steps,
        )

    def _compute_horizon(
            self,
            calibration: dict,
            coast_profiles: dict,
            dt: float,
    ) -> int:
        """Derive planning horizon from calibration data.

        Horizon = max(coast_duration) + τ/6, in steps.
        """
        max_coast_s = 0.0

        for dev_id, states in coast_profiles.items():
            for state, props in states.items():
                for prop, samples in props.items():
                    if samples:
                        last_t = samples[-1].get('elapsed_s', 0)
                        max_coast_s = max(max_coast_s, last_t)

        # Get envelope τ from any environment
        max_tau = 0.0
        for env_id, env_cal in calibration.items():
            envelope = env_cal.get('envelope', {})
            for prop, conductance in envelope.items():
                if conductance and conductance > 0:
                    tau = 1.0 / conductance  # τ = 1/conductance
                    max_tau = max(max_tau, tau)

        # Horizon in seconds, then convert to steps
        horizon_s = max_coast_s + max_tau / 6.0
        horizon_steps = max(
            self.MIN_HORIZON_STEPS,
            min(self.MAX_HORIZON_STEPS, int(horizon_s / dt) + 1))

        return horizon_steps

    def _simulate_trajectory(
            self,
            first_action: dict[str, str],
            current_readings: dict[str, dict[str, float]],
            ambient: dict[str, float],
            calibration: dict,
            device_env_map: dict[str, str],
            current_device_states: dict[str, str],
            coast_profiles: dict,
            targets: dict,
            env_safety: dict,
            device_ids: list[str],
            state_lists: list[list[str]],
            schedule_overrides: dict[str, str],
            locked_out_devices: set[str],
            device_amps: dict | None,
            horizon: int,
            dt: float,
    ) -> TrajectoryResult:
        """Simulate a trajectory starting with first_action.

        Steps 2..N use greedy one-step lookahead to pick actions.
        """
        steps = []
        total_cost = 0.0

        # State tracking for coast
        sim_device_states = dict(current_device_states)
        # Track when each device was turned off for coast interpolation
        # {device_id: (step_turned_off, state_it_was_in)}
        coast_events: dict[str, tuple[int, str]] = {}

        # Working copy of readings
        sim_readings = {
            env_id: dict(props)
            for env_id, props in current_readings.items()
        }

        for step_i in range(horizon):
            # First step uses the candidate action;
            # subsequent steps use greedy best
            if step_i == 0:
                action = first_action
            else:
                action = self._greedy_action(
                    sim_readings, ambient, calibration,
                    device_env_map, sim_device_states,
                    coast_events, coast_profiles,
                    targets, env_safety,
                    device_ids, state_lists,
                    schedule_overrides, locked_out_devices,
                    device_amps, dt, step_i)

            # Detect coast events: device was on, now turning off
            for dev_id, new_state in action.items():
                old_state = sim_device_states.get(dev_id, 'off')
                if old_state != 'off' and new_state == 'off':
                    coast_events[dev_id] = (step_i, old_state)
                elif new_state != 'off':
                    # Device is on — clear any pending coast
                    coast_events.pop(dev_id, None)

            # Save pre-step readings for baseline computation
            prev_readings = {
                eid: dict(p) for eid, p in sim_readings.items()
            }

            # Simulate one step with the chosen action
            sim_readings = self._simulate_step(
                sim_readings, ambient, calibration,
                device_env_map, action,
                coast_events, coast_profiles,
                dt, step_i)

            # Compute baseline: what would happen with only
            # schedule-forced devices (no optional devices).
            # This is used to mask side-effects in scoring.
            passive_action = {}
            for did in device_ids:
                if did in schedule_overrides:
                    passive_action[did] = schedule_overrides[did]
                else:
                    passive_action[did] = 'off'
            baseline = self._simulate_step(
                prev_readings, ambient, calibration,
                device_env_map, passive_action,
                coast_events, coast_profiles,
                dt, step_i)

            # Score with role-aware side-effect filtering
            step_cost = self._score(
                sim_readings, targets, env_safety,
                action=action, baseline=baseline)
            discount = self.DISCOUNT ** step_i
            total_cost += step_cost * discount

            steps.append(TrajectoryStep(
                step=step_i,
                elapsed_s=step_i * dt,
                readings={eid: dict(p) for eid, p in sim_readings.items()},
                action=dict(action),
                cost=step_cost))

            # Update device states for next step
            sim_device_states = dict(action)

        return TrajectoryResult(
            first_action=first_action,
            trajectory_cost=total_cost,
            steps=steps)

    def _simulate_step(
            self,
            readings: dict[str, dict[str, float]],
            ambient: dict[str, float],
            calibration: dict,
            device_env_map: dict[str, str],
            action: dict[str, str],
            coast_events: dict[str, tuple[int, str]],
            coast_profiles: dict,
            dt: float,
            current_step: int,
    ) -> dict[str, dict[str, float]]:
        """Simulate one cycle of physics.

        Applies envelope decay, device contributions, and coast
        profile interpolation.  Returns new readings.
        """
        predicted = {}

        for env_id, env_readings in readings.items():
            if env_id == 'ambient':
                predicted[env_id] = dict(env_readings)
                continue

            env_cal = calibration.get(env_id)
            if env_cal is None:
                predicted[env_id] = dict(env_readings)
                continue

            env_predicted = {}
            envelope = env_cal.get('envelope', {})

            for prop, value in env_readings.items():
                pred = value

                # Envelope decay toward ambient
                conductance = envelope.get(prop)
                ambient_val = ambient.get(prop)
                if conductance and ambient_val is not None:
                    pred -= conductance * (value - ambient_val)

                env_predicted[prop] = pred

            # Device contributions (active devices)
            device_cals = env_cal.get('devices', {})
            for dev_id, state in action.items():
                if device_env_map.get(dev_id) != env_id:
                    continue
                if state == 'off':
                    continue
                dev_cal = device_cals.get(dev_id, {})
                state_effects = dev_cal.get(state, {})
                for prop, contribution in state_effects.items():
                    if prop in env_predicted:
                        env_predicted[prop] += contribution

            # Coast contributions (devices recently turned off)
            for dev_id, (off_step, was_state) in coast_events.items():
                if device_env_map.get(dev_id) != env_id:
                    continue
                # Only apply coast if device is still off
                if action.get(dev_id, 'off') != 'off':
                    continue

                elapsed_since_off = (current_step - off_step) * dt
                dev_profiles = coast_profiles.get(dev_id, {})
                state_profiles = dev_profiles.get(was_state, {})

                for prop, profile_data in state_profiles.items():
                    if prop not in env_predicted or not profile_data:
                        continue

                    # Interpolate the coast profile to get the
                    # delta from the shutoff value at this elapsed time
                    coast_delta = self._interpolate_coast(
                        profile_data, elapsed_since_off)
                    coast_delta_prev = self._interpolate_coast(
                        profile_data, elapsed_since_off - dt)

                    # The coast profile stores absolute values.
                    # The per-step contribution is the delta between
                    # current interpolated value and previous.
                    step_delta = coast_delta - coast_delta_prev
                    env_predicted[prop] += step_delta

            predicted[env_id] = env_predicted

        return predicted

    def _interpolate_coast(
            self,
            profile: list[dict],
            elapsed_s: float,
    ) -> float:
        """Interpolate coast profile to get delta from shutoff value.

        Profile is [{elapsed_s, value}].  Returns the delta from
        the first sample (shutoff value) at the given elapsed time.

        Beyond the end of the profile, returns the last delta
        (coast has peaked and is done).
        If elapsed_s <= 0, returns 0 (no coast yet).
        """
        if not profile or elapsed_s <= 0:
            return 0.0

        shutoff_val = profile[0]['value']

        # Beyond profile end: return last delta
        if elapsed_s >= profile[-1]['elapsed_s']:
            return profile[-1]['value'] - shutoff_val

        # Find surrounding samples and interpolate
        for i in range(len(profile) - 1):
            t0 = profile[i]['elapsed_s']
            t1 = profile[i + 1]['elapsed_s']
            if t0 <= elapsed_s <= t1:
                if t1 == t0:
                    return profile[i]['value'] - shutoff_val
                frac = (elapsed_s - t0) / (t1 - t0)
                v0 = profile[i]['value'] - shutoff_val
                v1 = profile[i + 1]['value'] - shutoff_val
                return v0 + frac * (v1 - v0)

        # Fallback
        return profile[-1]['value'] - shutoff_val

    def _greedy_action(
            self,
            readings: dict,
            ambient: dict,
            calibration: dict,
            device_env_map: dict,
            device_states: dict,
            coast_events: dict,
            coast_profiles: dict,
            targets: dict,
            env_safety: dict,
            device_ids: list[str],
            state_lists: list[list[str]],
            schedule_overrides: dict,
            locked_out_devices: set,
            device_amps: dict | None,
            dt: float,
            step_i: int,
    ) -> dict[str, str]:
        """Pick the best single-step action (greedy lookahead).

        Used for steps 2..N of the trajectory rollout.
        Applies primary-property constraints based on the simulated
        readings at this step.
        """
        # Apply property constraints to state lists for this step
        constrained_lists = []
        for did, states in zip(device_ids, state_lists):
            constrained = self._apply_property_constraint(
                did, states, readings, targets)
            constrained_lists.append(constrained)

        best_action = None
        best_cost = float('inf')

        for combo in itertools.product(*constrained_lists):
            candidate = dict(zip(device_ids, combo))

            if device_amps and not self._check_circuits(
                    candidate, device_amps):
                continue

            # Simulate one step with this candidate
            # Make a copy of coast events to test transitions
            test_coast = dict(coast_events)
            for dev_id, new_state in candidate.items():
                old_state = device_states.get(dev_id, 'off')
                if old_state != 'off' and new_state == 'off':
                    test_coast[dev_id] = (step_i, old_state)
                elif new_state != 'off':
                    test_coast.pop(dev_id, None)

            pred = self._simulate_step(
                readings, ambient, calibration,
                device_env_map, candidate,
                test_coast, coast_profiles,
                dt, step_i)

            # Compute baseline for side-effect filtering
            passive = {}
            for did in device_ids:
                if did in schedule_overrides:
                    passive[did] = schedule_overrides[did]
                else:
                    passive[did] = 'off'
            bl = self._simulate_step(
                readings, ambient, calibration,
                device_env_map, passive,
                coast_events, coast_profiles,
                dt, step_i)

            cost = self._score(
                pred, targets, env_safety,
                action=candidate, baseline=bl)
            if cost < best_cost:
                best_cost = cost
                best_action = candidate

        return best_action or {did: 'off' for did in device_ids}

    def _build_candidates(
            self,
            device_states_available: dict[str, list[str]],
            locked_out_devices: set[str],
            schedule_overrides: dict[str, str],
            readings: dict[str, dict[str, float]] | None = None,
            targets: dict[str, dict[str, dict]] | None = None,
    ) -> tuple[list[str], list[list[str]]]:
        """Build device ID list and per-device state options.

        Applies primary-property constraints: a device is excluded
        from non-off states if its primary property is already past
        the target boundary in the direction it pushes.  A heater
        cannot turn on when temperature exceeds target max.  A
        humidifier cannot turn on when humidity exceeds target max.
        """
        device_ids = []
        state_lists = []

        for device_id, available in device_states_available.items():
            device_ids.append(device_id)
            if device_id in schedule_overrides:
                state_lists.append([schedule_overrides[device_id]])
            elif device_id in locked_out_devices:
                safe = self._get_safe_state(device_id)
                state_lists.append([safe])
            else:
                filtered = self._apply_property_constraint(
                    device_id, available, readings, targets)
                state_lists.append(filtered)

        return device_ids, state_lists

    def _apply_property_constraint(
            self,
            device_id: str,
            available_states: list[str],
            readings: dict[str, dict[str, float]] | None,
            targets: dict[str, dict[str, dict]] | None,
    ) -> list[str]:
        """Remove non-off states if the device's primary property
        is already past the target boundary.

        A heater (increases temperature) is constrained to 'off'
        when temperature >= target_max.  A humidifier (increases
        humidity) is constrained to 'off' when humidity >= target_max.
        A fan (decreases temperature) is constrained to 'off' when
        temperature <= target_min.

        This is not a cost function tweak — it's a physical sanity
        constraint.  No reasonable controller turns a heater on in
        an overheated room.
        """
        if not readings or not targets:
            return available_states

        dev_cfg = self._devices.get(device_id, {})
        role = dev_cfg.get('role')
        if not role or role not in _ROLE_PRIMARY:
            return available_states

        primary_prop = _ROLE_PRIMARY[role]
        env_id = dev_cfg.get('environment')
        if not env_id:
            return available_states

        env_readings = readings.get(env_id, {})
        current_val = env_readings.get(primary_prop)
        if current_val is None:
            return available_states

        env_targets = targets.get(env_id, {})
        prop_target = env_targets.get(primary_prop, {})
        target_min = prop_target.get('min')
        target_max = prop_target.get('max')
        if target_min is None or target_max is None:
            return available_states

        # Determine direction this device pushes
        from spriggler.calibrate.precondition import ROLE_EFFECTS
        effect = ROLE_EFFECTS.get(role)
        if not effect:
            return available_states
        _, direction = effect

        if direction == 'increase' and current_val >= target_max:
            # Device pushes property UP but property is already
            # at or above target max.  Only allow 'off'.
            return ['off'] if 'off' in available_states else available_states

        if direction == 'decrease' and current_val <= target_min:
            # Device pushes property DOWN but property is already
            # at or below target min.  Only allow 'off'.
            return ['off'] if 'off' in available_states else available_states

        return available_states

    def _get_safe_state(self, device_id: str) -> str:
        dev_safety = self._safety.get('devices', {}).get(device_id, {})
        return dev_safety.get('safe_state', 'off')

    def _check_circuits(
            self,
            proposed: dict[str, str],
            device_amps: dict[str, dict[str, float]],
    ) -> bool:
        circuit_load: dict[str, float] = {}
        for device_id, state in proposed.items():
            device_cfg = self._devices.get(device_id, {})
            circuit_id = device_cfg.get('circuit')
            if not circuit_id:
                continue
            amps = device_amps.get(device_id, {}).get(state, 0.0)
            circuit_load[circuit_id] = (
                    circuit_load.get(circuit_id, 0.0) + amps)

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
            env_safety: dict,
            action: dict[str, str] | None = None,
            baseline: dict[str, dict[str, float]] | None = None,
    ) -> float:
        """Score predicted readings with role-aware side-effect filtering.

        The physics simulation includes ALL device effects (heater
        warms AND dries).  But the SCORING masks side-effects: for
        each active device, only its primary property's improvement
        counts toward the decision.  Side-effect changes are replaced
        with what would have happened WITHOUT that device (baseline).

        This prevents the planner from choosing a heater to fight
        humidity — the heater's drying effect is invisible to the
        cost function, so the decision is based purely on temperature.

        A small energy penalty is added per watt of power consumed.
        """
        # Build role-filtered readings for scoring
        if action and baseline:
            scored_readings = self._filter_side_effects(
                predicted, baseline, action)
        else:
            scored_readings = predicted

        total = 0.0
        for env_id, env_targets in targets.items():
            readings = scored_readings.get(env_id, {})
            limits = env_safety.get(env_id, {}).get('limits', {})
            total += compute_environment_cost(
                readings, env_targets, limits)

        # Energy penalty
        if action and self._power_watts:
            watts = 0.0
            for dev_id, state in action.items():
                dev_power = self._power_watts.get(dev_id, {})
                watts += dev_power.get(state, 0.0)
            total += watts * self.ENERGY_PENALTY

        return total

    def _filter_side_effects(
            self,
            predicted: dict[str, dict[str, float]],
            baseline: dict[str, dict[str, float]],
            action: dict[str, str],
    ) -> dict[str, dict[str, float]]:
        """Replace side-effect property values with baseline values.

        For each active device, identify its primary property (from
        role).  All OTHER properties in that device's environment get
        their values replaced with the baseline (what would happen
        without that device).  The primary property keeps the
        predicted value.

        If multiple devices are active in the same environment, each
        device's primary property is kept.  Properties that are no
        device's primary property get the baseline value.
        """
        # Collect primary properties per environment
        # {env_id: set of primary property names}
        env_primaries: dict[str, set] = {}
        for dev_id, state in action.items():
            if state == 'off':
                continue
            role = self._devices.get(dev_id, {}).get('role')
            if role and role in _ROLE_PRIMARY:
                env_id = self._devices.get(dev_id, {}).get(
                    'environment')
                if env_id:
                    env_primaries.setdefault(env_id, set()).add(
                        _ROLE_PRIMARY[role])

        # Build filtered readings
        result = {}
        for env_id, props in predicted.items():
            primaries = env_primaries.get(env_id, set())
            if not primaries:
                # No active devices in this env — use predicted as-is
                result[env_id] = dict(props)
                continue

            filtered = {}
            bl = baseline.get(env_id, {})
            for prop, value in props.items():
                if prop in primaries:
                    # This is a primary property — keep predicted
                    filtered[prop] = value
                else:
                    # Side effect — use baseline value instead
                    filtered[prop] = bl.get(prop, value)
            result[env_id] = filtered

        return result