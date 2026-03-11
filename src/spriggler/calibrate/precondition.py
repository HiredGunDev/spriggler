"""Pre-conditioning for device calibration.

Before characterizing a device, the environment must be at conditions
where the device's effect can be meaningfully measured.  This module
drives the environment to the operational starting point using
available devices (heater, fan), then waits for conditions to settle.

The target starting point is derived from the schedule: characterize
a device at the boundary of its operational envelope in the direction
opposite to its effect.  A heater (pushes temp up) starts at temp min.
A humidifier (pushes humidity up) starts at humidity min.

Pre-conditioning is uncalibrated — it's bang-bang control toward a
target, watching the sensor until arrival.  No calibration data needed.
"""

import logging
import time

log = logging.getLogger(__name__)


# What property each role primarily affects and in which direction
ROLE_EFFECTS = {
    'heater':       ('temperature', 'increase'),
    'cooler':       ('temperature', 'decrease'),
    'humidifier':   ('humidity', 'increase'),
    'dehumidifier': ('humidity', 'decrease'),
    'exhaust':      ('temperature', 'decrease'),
    'intake':       ('temperature', 'decrease'),
    'circulation':  ('temperature', 'decrease'),
    'light':        ('temperature', 'increase'),
}


def compute_starting_targets(device_role, schedule_targets):
    """Compute the ideal starting conditions for characterizing a device.

    The device will push its primary property in one direction.
    We want to start at the opposite end of the operational envelope
    so there's maximum room to measure the effect.

    Args:
        device_role: Device role string (e.g., 'heater', 'humidifier').
        schedule_targets: Combined schedule targets across all phases.
            {property: {'min': value, 'max': value}}

    Returns:
        {property: target_value} — conditions to achieve before
        characterization begins.  Only includes properties the
        device affects.
    """
    effect = ROLE_EFFECTS.get(device_role)
    if effect is None:
        return {}

    primary_prop, direction = effect
    targets_for_prop = schedule_targets.get(primary_prop)
    if targets_for_prop is None:
        return {}

    if direction == 'increase':
        # Device pushes up → start at min
        return {primary_prop: targets_for_prop['min']}
    else:
        # Device pushes down → start at max
        return {primary_prop: targets_for_prop['max']}


def compute_schedule_envelope(config, env_id):
    """Compute the widest operational envelope across all schedule phases.

    Takes the min of all mins and max of all maxes across phases,
    giving the full range the environment operates in.

    Args:
        config: Loaded config dict (values already in SI).
        env_id: Environment ID.

    Returns:
        {property: {'min': value, 'max': value}}
    """
    schedule = config.get('schedules', {}).get(env_id, {})
    phases = schedule.get('phases', [])

    envelope = {}
    for phase in phases:
        for prop, targets in phase.get('targets', {}).items():
            if prop not in envelope:
                envelope[prop] = {
                    'min': targets['min'],
                    'max': targets['max'],
                }
            else:
                envelope[prop]['min'] = min(
                    envelope[prop]['min'], targets['min'])
                envelope[prop]['max'] = max(
                    envelope[prop]['max'], targets['max'])

    return envelope


def check_reachability(starting_targets, current_readings, ambient,
                       safety_limits, env_id, available_devices):
    """Check whether starting targets are reachable.

    Args:
        starting_targets: {property: target_value}
        current_readings: Current environment sensor data.
        ambient: Current ambient sensor data.
        safety_limits: {env_id: {prop: {absolute_min, absolute_max}}}
        env_id: Environment being conditioned.
        available_devices: List of (device_id, role) tuples for
            devices in this environment that can be used.

    Returns:
        (reachable: bool, reason: str or None)
    """
    has_heater = any(role == 'heater' for _, role in available_devices)
    has_fan = any(role in ('exhaust', 'intake', 'fan', 'vent')
                  for _, role in available_devices)

    for prop, target in starting_targets.items():
        current = current_readings.get(prop)
        amb_val = ambient.get(prop)

        if current is None:
            return False, f"No sensor reading for {prop}"

        if prop == 'temperature':
            if target > current:
                # Need to warm
                if not has_heater:
                    return False, (
                        f"Need to warm to {target:.1f} but no heater "
                        f"available")
                # Heater can always warm (up to safety max)
                safe_max = safety_limits.get(env_id, {}).get(
                    prop, {}).get('absolute_max')
                if safe_max and target > safe_max:
                    return False, (
                        f"Target temp {target:.1f} exceeds safety "
                        f"max {safe_max:.1f}")
            elif target < current:
                # Need to cool — fan only works if ambient is below target
                if amb_val is None or amb_val >= target:
                    if not has_heater:
                        return False, (
                            f"Need to cool to {target:.1f} but ambient "
                            f"is {amb_val:.1f} and no way to cool below "
                            f"ambient")
                    # Can't cool below ambient — but if we're warming
                    # we don't need to cool
                elif not has_fan:
                    return False, (
                        f"Need to cool to {target:.1f} but no fan "
                        f"available")

        elif prop == 'humidity':
            if target < current:
                # Need to dry.  Heating warms air, drops RH.
                # Fan exchanges with ambient (may or may not be drier).
                if not has_heater and not has_fan:
                    return False, (
                        f"Need to dry to {target:.1f}% but no heater "
                        f"or fan available")
                # Check if drying is possible: ambient at operating
                # temp would have lower RH than current
                # This is approximately checkable: if ambient temp
                # is much lower and ambient RH isn't extreme, heating
                # will drop RH.  Hard to be precise without psychrometrics,
                # so be optimistic if we have a heater.
                if not has_heater and amb_val is not None:
                    if amb_val >= target:
                        return False, (
                            f"Need humidity below {target:.1f}% but "
                            f"ambient is {amb_val:.1f}% and no heater "
                            f"to dry the air")
            elif target > current:
                # Need to humidify for pre-conditioning — unusual.
                # Only needed if characterizing a dehumidifier.
                # Skip for now.
                return False, (
                    f"Need to raise humidity to {target:.1f}% — "
                    f"pre-conditioning for dehumidifiers not "
                    f"implemented")

    return True, None


def precondition(targets, current_readings, ambient, env_id,
                 config, safety_limits, read_all, check_safety,
                 fmt_t, fmt_d, display_unit, device_id_being_calibrated,
                 sample_interval=15, max_time=1200):
    """Drive environment to target starting conditions.

    Uses available heater and fan to push environment toward targets.
    Uncalibrated bang-bang control — just drive toward the number.

    After reaching targets, waits for conditions to settle (rates
    approach zero) before returning.

    Args:
        targets: {property: target_value} in SI units.
        current_readings: Current {property: value} for the environment.
        ambient: Current {property: value} for ambient.
        env_id: Environment ID.
        config: Full loaded config.
        safety_limits: {env_id: {prop: {absolute_min, absolute_max}}}
        read_all: Callable that returns a MultiEnvSample.
        check_safety: Safety check callable.
        fmt_t, fmt_d: Formatting functions.
        display_unit: 'F' or 'C'.
        device_id_being_calibrated: Skip this device (don't use it
            for pre-conditioning).
        sample_interval: Seconds between sensor reads.
        max_time: Maximum pre-conditioning time in seconds.

    Returns:
        True if targets reached, False if timed out or failed.
    """
    from spriggler.devices.registry import get_device_driver

    # Find available pre-conditioning devices in this environment
    heater_driver = None
    heater_id = None
    fan_driver = None
    fan_id = None

    for did, dcfg in config['devices'].items():
        if did == device_id_being_calibrated:
            continue
        if dcfg['environment'] != env_id:
            continue
        role = dcfg.get('role', '')
        if role == 'heater' and heater_driver is None:
            heater_driver = get_device_driver(
                dcfg['driver'])(dcfg['driver_config'])
            heater_id = did
        elif role in ('exhaust', 'intake', 'fan', 'vent') and \
                fan_driver is None:
            fan_driver = get_device_driver(
                dcfg['driver'])(dcfg['driver_config'])
            fan_id = did

    temp_target = targets.get('temperature')
    hum_target = targets.get('humidity')

    print(f"\n── Pre-conditioning ──")
    if temp_target is not None:
        print(f"  Temperature target: {fmt_t(temp_target)}")
    if hum_target is not None:
        print(f"  Humidity target: {hum_target:.1f}%")
    if heater_id:
        print(f"  Heater: {heater_id}")
    if fan_id:
        print(f"  Fan: {fan_id}")

    start_time = time.time()
    last_print = 0
    heater_delay = 45  # seconds — run fan first to start air exchange
    # before heater creates humidity transient

    # During pre-conditioning, only enforce temperature safety.
    # Humidity transients are expected (heater evaporates condensation
    # off surfaces, causing a brief humidity spike before warming
    # drops RH).  The calibration safety check would kill the heater
    # during this transient, preventing us from ever reaching target.
    temp_safety_max = safety_limits.get(env_id, {}).get(
        'temperature', {}).get('absolute_max')
    temp_safety_min = safety_limits.get(env_id, {}).get(
        'temperature', {}).get('absolute_min')

    # Phase 1: Drive toward targets
    try:
        while time.time() - start_time < max_time:
            sample = read_all()
            env = sample.environments.get(env_id, {})
            amb = sample.environments.get('ambient', {})
            elapsed = time.time() - start_time
            mins = int(elapsed) // 60
            secs = int(elapsed) % 60

            cur_temp = env.get('temperature')
            cur_hum = env.get('humidity')
            amb_temp = amb.get('temperature')

            # Temperature-only safety check
            if cur_temp is not None:
                if temp_safety_max and cur_temp > temp_safety_max:
                    if heater_driver:
                        heater_driver.set_state('off')
                    print(f"  Temperature safety limit reached "
                          f"({fmt_t(cur_temp)}). Heater off.")
                    # Don't abort — fan can still run to cool
                if temp_safety_min and cur_temp < temp_safety_min:
                    if fan_driver:
                        fan_driver.set_state('off')
                    print(f"  Temperature safety min reached "
                          f"({fmt_t(cur_temp)}). Fan off.")

            # Decide what to run
            need_heat = False
            need_fan = False

            if temp_target is not None and cur_temp is not None:
                if cur_temp < temp_target - 0.5:  # below target
                    need_heat = True
                elif cur_temp > temp_target + 2.0:  # well above
                    if amb_temp is not None and amb_temp < cur_temp:
                        need_fan = True

            if hum_target is not None and cur_hum is not None:
                if cur_hum > hum_target + 2.0:  # too humid
                    need_heat = True
                    # Fan if ambient is cooler (drier absolute humidity
                    # when warmed)
                    if amb_temp is not None and amb_temp < cur_temp:
                        need_fan = True

            # Fan starts immediately; heater delayed to let fan
            # begin air exchange before the humidity transient
            if fan_driver:
                if need_fan:
                    fan_driver.set_state('on')
                else:
                    fan_driver.set_state('off')

            if heater_driver:
                if need_heat and elapsed >= heater_delay:
                    heater_driver.set_state('on')
                elif not need_heat:
                    heater_driver.set_state('off')

            # Status
            parts = []
            if cur_temp is not None:
                parts.append(f"T={fmt_t(cur_temp)}")
            if cur_hum is not None:
                parts.append(f"H={cur_hum:.1f}%")
            active = []
            if need_heat and heater_driver:
                if elapsed >= heater_delay:
                    active.append('heater')
                else:
                    active.append(f'heater in {int(heater_delay - elapsed)}s')
            if need_fan and fan_driver:
                active.append('fan')
            if active:
                parts.append(f"[{'+'.join(active)}]")

            if int(elapsed) // 30 > last_print:
                last_print = int(elapsed) // 30
                print(f"  [{mins:02d}:{secs:02d}] {'  '.join(parts)}")

            # Check if we've reached targets
            reached = True
            if temp_target is not None and cur_temp is not None:
                if abs(cur_temp - temp_target) > 2.0:  # within 2K
                    reached = False
            if hum_target is not None and cur_hum is not None:
                if abs(cur_hum - hum_target) > 3.0:  # within 3%RH
                    reached = False

            if reached and not need_heat and not need_fan:
                print(f"  Targets reached at {mins}m{secs}s.")
                break

            time.sleep(sample_interval)
        else:
            print(f"  Pre-conditioning timeout "
                  f"({max_time // 60}min). Proceeding.")

    finally:
        # Always shut down pre-conditioning devices
        if heater_driver:
            heater_driver.set_state('off')
        if fan_driver:
            fan_driver.set_state('off')

    # Phase 2: Settle — wait for coast to dissipate and rates to
    # approach zero.  Watch the primary property; when 3 consecutive
    # readings show < 0.1 unit change, we're settled.
    print(f"  Settling...")
    settle_start = time.time()
    max_settle = 300  # 5 min max settle
    prev_vals = {}  # {prop: last_value}
    stable_count = {}  # {prop: consecutive_stable_readings}

    props_to_watch = list(targets.keys())

    while time.time() - settle_start < max_settle:
        time.sleep(sample_interval)
        sample = read_all()
        env = sample.environments.get(env_id, {})
        elapsed_total = time.time() - start_time
        elapsed_settle = time.time() - settle_start
        mins = int(elapsed_total) // 60
        secs = int(elapsed_total) % 60

        parts = []
        all_stable = True

        for prop in props_to_watch:
            val = env.get(prop)
            if val is None:
                continue

            if prop == 'temperature':
                parts.append(f"T={fmt_t(val)}")
            elif prop == 'humidity':
                parts.append(f"H={val:.1f}%")

            if prop in prev_vals:
                delta = abs(val - prev_vals[prop])
                threshold = 0.3 if prop == 'temperature' else 0.5
                if delta < threshold:
                    stable_count[prop] = stable_count.get(prop, 0) + 1
                else:
                    stable_count[prop] = 0
            prev_vals[prop] = val

            if stable_count.get(prop, 0) < 3:
                all_stable = False

        if int(elapsed_settle) // 30 > (int(elapsed_settle) - sample_interval) // 30:
            status = "stable" if all_stable else "settling"
            print(f"  [{mins:02d}:{secs:02d}] {'  '.join(parts)}  "
                  f"({status})")

        if all_stable and elapsed_settle >= 60:  # at least 1 min settle
            print(f"  Settled at {int(elapsed_settle)}s.")
            return True

    print(f"  Settle timeout. Proceeding.")
    return True