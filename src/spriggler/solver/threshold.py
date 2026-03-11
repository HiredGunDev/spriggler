"""Threshold Controller — calibration-informed hysteresis control.

Replaces the trajectory planner with simple, predictable logic.
Each device is governed by thresholds computed from calibration data:

    turn_off_threshold = target_setpoint - coast_overshoot
    turn_on_threshold  = turn_off_threshold - hysteresis_band

The calibration data (rates, coast profiles, envelope τ) determines
the thresholds.  The controller applies them.  No cost functions,
no trajectory simulation, no greedy rollout.

For multi-device environments, each device is evaluated independently
against its primary property.  Conflicts (heater wants ON but temp
is already high) are prevented by the threshold logic itself — the
heater's turn-off threshold accounts for coast, so it shuts off
before the coast carries temperature past target max.

Device roles determine which property each device controls:
    heater, light         → temperature (increase)
    cooler, exhaust, fan  → temperature (decrease)
    humidifier            → humidity (increase)
    dehumidifier          → humidity (decrease)

Schedule overrides (lights on/off) are respected.
Safety lockouts are respected.
Circuit limits are checked.
"""

from dataclasses import dataclass


# Map device role to (primary_property, direction)
_ROLE_EFFECTS = {
    'heater':       ('temperature', 'increase'),
    'cooler':       ('temperature', 'decrease'),
    'humidifier':   ('humidity', 'increase'),
    'dehumidifier': ('humidity', 'decrease'),
    'exhaust':      ('temperature', 'decrease'),
    'intake':       ('temperature', 'decrease'),
    'circulation':  ('temperature', 'decrease'),
    'light':        ('temperature', 'increase'),
}


@dataclass
class ControllerResult:
    """The controller's recommendation for one control cycle."""
    device_states: dict[str, str]
    # For compatibility with daemon logging
    total_cost: float = 0.0
    feasible_count: int = 0
    total_count: int = 0


class ThresholdController:
    """Calibration-informed hysteresis controller.

    Each device has a turn-on and turn-off threshold for its
    primary property, computed from calibration data.  The
    controller checks current readings against thresholds and
    returns the desired device states.

    Thresholds for devices that INCREASE a property:
        turn_off = target_max - coast_overshoot
        turn_on  = target_min + hysteresis_band

    Thresholds for devices that DECREASE a property:
        turn_off = target_min + coast_overshoot (coast is negative)
        turn_on  = target_max - hysteresis_band

    The hysteresis band prevents chattering.  It's derived from
    the device's rate × cycle_time — roughly one cycle's worth
    of change.  Minimum 0.5K / 1%RH to prevent noise-driven
    switching.
    """

    # Minimum hysteresis band to prevent noise-driven switching
    MIN_HYSTERESIS_K = 0.5     # ~0.9°F
    MIN_HYSTERESIS_RH = 1.0    # 1%RH

    def __init__(self, config: dict) -> None:
        self._config = config
        self._devices = config['devices']
        self._circuits = config.get('circuits', {})
        self._safety = config.get('safety', {})

    def decide(
            self,
            readings: dict[str, dict[str, float]],
            ambient: dict[str, float],
            targets: dict[str, dict[str, dict]],
            calibration: dict,
            device_env_map: dict[str, str],
            current_device_states: dict[str, str],
            schedule_overrides: dict[str, str],
            locked_out_devices: set[str],
            coast_data: dict[str, dict],
            device_amps: dict[str, dict[str, float]] | None = None,
            cycle_seconds: float = 15.0,
    ) -> ControllerResult:
        """Decide device states using threshold logic.

        Args:
            readings: {env_id: {property: value}}
            ambient: {property: value}
            targets: {env_id: {property: {min, max, ideal}}}
            calibration: {env_id: {devices: {dev_id: {state: {prop: rate}}}}}
            device_env_map: {device_id: env_id}
            current_device_states: {device_id: state}
            schedule_overrides: {device_id: state}
            locked_out_devices: set of device IDs
            coast_data: {device_id: {property: {overshoot, duration}}}
            device_amps: {device_id: {state: amps}}
            cycle_seconds: seconds per cycle

        Returns:
            ControllerResult with device states.
        """
        result = {}

        for dev_id, dev_cfg in self._devices.items():
            # Schedule overrides take priority
            if dev_id in schedule_overrides:
                result[dev_id] = schedule_overrides[dev_id]
                continue

            # Safety lockouts
            if dev_id in locked_out_devices:
                safe = self._get_safe_state(dev_id)
                result[dev_id] = safe
                continue

            role = dev_cfg.get('role')
            effect = _ROLE_EFFECTS.get(role)
            if not effect:
                result[dev_id] = current_device_states.get(dev_id, 'off')
                continue

            primary_prop, direction = effect
            env_id = dev_cfg.get('environment')
            if not env_id:
                result[dev_id] = 'off'
                continue

            # Get current value of primary property
            env_readings = readings.get(env_id, {})
            current_val = env_readings.get(primary_prop)
            if current_val is None:
                result[dev_id] = current_device_states.get(dev_id, 'off')
                continue

            # Get targets
            prop_targets = targets.get(env_id, {}).get(primary_prop, {})
            target_min = prop_targets.get('min')
            target_max = prop_targets.get('max')
            if target_min is None or target_max is None:
                result[dev_id] = current_device_states.get(dev_id, 'off')
                continue

            # Get coast overshoot for this device's primary property
            dev_coast = coast_data.get(dev_id, {})
            coast_info = dev_coast.get(primary_prop, {})
            overshoot = abs(coast_info.get('overshoot', 0))

            # Get the device's rate for hysteresis calculation
            env_cal = calibration.get(env_id, {})
            dev_cal = env_cal.get('devices', {}).get(dev_id, {})
            # Use the highest non-off state's rate
            max_rate = 0
            best_state = 'off'
            available = list(dev_cal.keys())
            for state, effects in dev_cal.items():
                if state == 'off':
                    continue
                rate = abs(effects.get(primary_prop, 0))
                if rate > max_rate:
                    max_rate = rate
                    best_state = state

            # Hysteresis: one cycle's worth of change, minimum floor
            min_hyst = (self.MIN_HYSTERESIS_K if primary_prop == 'temperature'
                        else self.MIN_HYSTERESIS_RH)
            hysteresis = max(min_hyst, max_rate)

            # Compute thresholds
            is_on = current_device_states.get(dev_id, 'off') != 'off'

            if direction == 'increase':
                # Device pushes property UP (heater, humidifier)
                # Turn off before coast carries past target max
                turn_off = target_max - overshoot
                # Turn on with hysteresis below turn_off point
                turn_on = turn_off - hysteresis

                # Clamp: turn_on shouldn't go below target_min
                turn_on = max(turn_on, target_min)

                if is_on:
                    # Currently on: turn off if above turn_off
                    if current_val >= turn_off:
                        result[dev_id] = 'off'
                    else:
                        result[dev_id] = current_device_states[dev_id]
                else:
                    # Currently off: turn on if below turn_on
                    if current_val <= turn_on:
                        result[dev_id] = best_state
                    else:
                        result[dev_id] = 'off'

            else:
                # Device pushes property DOWN (fan, cooler, dehumidifier)
                # Turn off before coast carries past target min
                turn_off = target_min + overshoot
                # Turn on with hysteresis above turn_off
                turn_on = turn_off + hysteresis

                # Clamp: turn_on shouldn't go above target_max
                turn_on = min(turn_on, target_max)

                if is_on:
                    if current_val <= turn_off:
                        result[dev_id] = 'off'
                    else:
                        result[dev_id] = current_device_states[dev_id]
                else:
                    if current_val >= turn_on:
                        result[dev_id] = best_state
                    else:
                        result[dev_id] = 'off'

        # Check circuit limits — if over, shed lowest-priority device
        if device_amps:
            result = self._enforce_circuits(result, device_amps)

        return ControllerResult(
            device_states=result,
            feasible_count=len(result),
            total_count=len(result),
        )

    def _get_safe_state(self, device_id: str) -> str:
        dev_safety = self._safety.get('devices', {}).get(device_id, {})
        return dev_safety.get('safe_state', 'off')

    def _enforce_circuits(
            self,
            states: dict[str, str],
            device_amps: dict[str, dict[str, float]],
    ) -> dict[str, str]:
        """If any circuit is over its amp limit, turn off the
        highest-draw non-schedule device until under limit."""
        result = dict(states)

        for circuit_id, circuit_cfg in self._circuits.items():
            max_amps = circuit_cfg.get('max_amps', float('inf'))

            while True:
                load = 0.0
                devices_on = []
                for dev_id, state in result.items():
                    dev_cfg = self._devices.get(dev_id, {})
                    if dev_cfg.get('circuit') != circuit_id:
                        continue
                    amps = device_amps.get(dev_id, {}).get(state, 0)
                    load += amps
                    if state != 'off':
                        devices_on.append((dev_id, amps))

                if load <= max_amps:
                    break

                if not devices_on:
                    break

                # Shed highest-draw device
                devices_on.sort(key=lambda x: -x[1])
                shed_id = devices_on[0][0]
                result[shed_id] = 'off'

        return result