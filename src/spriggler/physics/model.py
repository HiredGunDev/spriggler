"""Physics model - predicts future environment state.

The physics model is a calculator, not a learner. It takes calibration
data (learned during the calibration phase) and computes predictions
using simple energy balance equations.

For each environment, the model computes:

    predicted = current
                - conductance × (current - ambient)   # envelope loss
                + Σ device_contributions               # active devices

This captures the two dominant forces acting on an enclosed environment:
1. Heat/moisture exchange with the outside (envelope loss)
2. Energy added or removed by devices

The model operates in user units (°F or °C, %RH). Calibration data
is stored in user units. The solver calls this model for every
candidate device combination to predict where each environment
will be after one control cycle.

Design principles:
    - Pure function: no state, no side effects, no I/O
    - Operates on dicts, not objects — easy to test, easy to serialize
    - Ambient is an input, not something the model tracks
    - Unknown devices or environments are silently ignored
    - Missing calibration data produces no prediction (returns current)
"""


def predict(
    current_readings: dict[str, dict[str, float]],
    ambient: dict[str, float],
    proposed_states: dict[str, str],
    calibration: dict,
    device_env_map: dict[str, str],
) -> dict[str, dict[str, float]]:
    """Predict environment state after one control cycle.

    Args:
        current_readings: {env_id: {property: value}}
            Current sensor readings for each environment.
        ambient: {property: value}
            Current ambient (outdoor) readings.
        proposed_states: {device_id: state_name}
            The device states being evaluated.
        calibration: Calibration data dict with structure:
            {env_id: {
                "envelope": {property: conductance_coefficient},
                "devices": {device_id: {state: {property: contribution}}}
            }}
        device_env_map: {device_id: env_id}
            Which environment each device belongs to.

    Returns:
        {env_id: {property: predicted_value}} for every environment
        that has both current readings and calibration data.
    """
    predicted = {}

    for env_id, readings in current_readings.items():
        if env_id == 'ambient':
            continue

        env_cal = calibration.get(env_id)
        if env_cal is None:
            # No calibration for this environment — return current unchanged
            predicted[env_id] = dict(readings)
            continue

        env_predicted = {}
        envelope = env_cal.get('envelope', {})

        for prop, value in readings.items():
            # Start with current value
            pred = value

            # Apply envelope loss toward ambient
            conductance = envelope.get(prop)
            ambient_value = ambient.get(prop)
            if conductance is not None and ambient_value is not None:
                pred -= conductance * (value - ambient_value)

            env_predicted[prop] = pred

        # Apply device contributions
        device_cals = env_cal.get('devices', {})
        for device_id, state in proposed_states.items():
            # Only apply if this device belongs to this environment
            if device_env_map.get(device_id) != env_id:
                continue

            dev_cal = device_cals.get(device_id, {})
            state_effects = dev_cal.get(state, {})
            for prop, contribution in state_effects.items():
                if prop in env_predicted:
                    env_predicted[prop] += contribution

        predicted[env_id] = env_predicted

    return predicted


def make_solver_predict_fn(
    ambient: dict[str, float],
    calibration: dict,
    device_env_map: dict[str, str],
):
    """Create a predict_fn suitable for passing to the solver.

    The solver expects: predict_fn(current_readings, proposed_states)
    This factory curries the ambient, calibration, and device_env_map
    so the solver doesn't need to know about them.

    Args:
        ambient: Current ambient readings.
        calibration: Calibration data dict.
        device_env_map: {device_id: env_id} mapping.

    Returns:
        A callable(current_readings, proposed_states) -> predicted_readings.
    """
    def solver_predict(current_readings, proposed_states):
        return predict(
            current_readings=current_readings,
            ambient=ambient,
            proposed_states=proposed_states,
            calibration=calibration,
            device_env_map=device_env_map,
        )
    return solver_predict

