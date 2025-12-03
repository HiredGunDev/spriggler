"""Environment control loop for Spriggler."""

from __future__ import annotations

import datetime as _dt
import time
from inspect import isawaitable
from typing import Dict, Iterable, List, Mapping, Optional

from loguru import logger

from devices.power_state import PowerCommandResult


class EnvironmentController:
    """Decide how actuators should respond to current sensor readings."""

    def __init__(
        self,
        *,
        config: Mapping,
        log_callback=None,
        debounce_seconds: float = 5.0,
        state_refresh_seconds: float = 60.0,
        dry_run: bool = False,
    ) -> None:
        self.environments = config.get("environments", {}).get("definitions", [])
        self.schedules = {
            schedule.get("id"): schedule
            for schedule in config.get("schedules", {}).get("definitions", [])
        }
        self.device_definitions = {
            definition.get("id"): definition
            for definition in config.get("devices", {}).get("definitions", [])
        }
        self.device_effect_defaults = (
            config.get("devices", {}).get("defaults", {}).get("effects", {})
        )
        self.debounce_seconds = debounce_seconds
        self.state_refresh_seconds = state_refresh_seconds
        self.dry_run = dry_run
        self._log_callback = log_callback
        self._last_commands: Dict[tuple[str, str], tuple[str, float]] = {}
        self._last_property_logs: Dict[tuple[str, str], tuple[float, str, object, object]] = {}
        self._missing_reading_logs: Dict[tuple[str, str], float] = {}
        self._validate_configuration()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _validate_configuration(self) -> None:
        """
        Validate that:
        - Every controller device used by an environment/property has at least one effect.
        - Each effect that applies to that property has a 'policy' mapping.
        - Each policy defines all expected decisions: 'increase', 'decrease', 'stable'.
        Fail fast if anything is missing.
        """
        required_decisions = {"increase", "decrease", "stable"}
        errors: List[str] = []

        for environment in self.environments:
            env_id = environment.get("id", "environment")
            properties = environment.get("properties", {})

            for property_name, property_config in properties.items():
                controllers = property_config.get("controllers", [])

                for device_id in controllers:
                    effects = self._device_effects(device_id)
                    if not effects:
                        errors.append(
                            f"[{env_id}.{property_name}] Device '{device_id}' has no declared effects."
                        )
                        continue

                    matching_effects = [
                        effect for effect in effects if effect.get("property") == property_name
                    ]
                    if not matching_effects:
                        errors.append(
                            f"[{env_id}.{property_name}] Device '{device_id}' has effects, "
                            f"but none for property '{property_name}'."
                        )
                        continue

                    for effect in matching_effects:
                        policy = effect.get("policy")
                        if not isinstance(policy, Mapping):
                            errors.append(
                                f"[{env_id}.{property_name}] Device '{device_id}' effect for "
                                f"'{property_name}' is missing a 'policy' mapping."
                            )
                            continue

                        missing = required_decisions - set(policy.keys())
                        if missing:
                            errors.append(
                                f"[{env_id}.{property_name}] Device '{device_id}' policy for "
                                f"'{property_name}' missing decisions: {sorted(missing)}."
                            )

        if errors:
            error_msg = "Invalid controller configuration:\n" + "\n".join(f"- {e}" for e in errors)
            raise ValueError(error_msg)

    async def evaluate(
        self,
        *,
        sensor_data: Mapping[str, object],
        devices: Mapping[str, object],
    ) -> None:
        """Evaluate environments and issue device commands when necessary."""

        for environment in self.environments:
            environment_id = environment.get("id", "environment")
            properties = environment.get("properties", {})

            for property_name, property_config in properties.items():
                schedule = self._select_schedule(
                    property_name, property_config.get("schedules", [])
                )
                if not schedule:
                    self._log(
                        f"No active schedule found for property '{property_name}'",
                        level="INFO",
                        entity=environment_id,
                    )
                    continue

                target_range = schedule.get("targets", {}).get(property_name)
                if not target_range:
                    self._log(
                        f"Schedule '{schedule.get('id')}' missing targets for '{property_name}'",
                        level="WARNING",
                        entity=environment_id,
                    )
                    continue

                if isinstance(target_range, str):
                    desired_state = target_range.strip().lower()
                    if desired_state not in {"on", "off"}:
                        self._log(
                            (
                                f"Unsupported target '{target_range}' for property "
                                f"'{property_name}'"
                            ),
                            level="WARNING",
                            entity=environment_id,
                        )
                        continue

                    await self._apply_state_targets(
                        environment_id=environment_id,
                        property_name=property_name,
                        desired_state=desired_state,
                        controllers=property_config.get("controllers", []),
                        devices=devices,
                    )
                    continue

                property_value = self._aggregate_sensor_values(
                    property_name, property_config.get("sensors", []), sensor_data
                )

                if property_value is None:
                    missing_key = (environment_id, property_name)
                    now = time.monotonic()
                    last_missing = self._missing_reading_logs.get(missing_key)

                    if last_missing is None or now - last_missing >= self.state_refresh_seconds:
                        self._log(
                            f"No readings available for property '{property_name}'",
                            level="WARNING",
                            entity=environment_id,
                        )
                        self._missing_reading_logs[missing_key] = now

                    continue

                self._missing_reading_logs.pop((environment_id, property_name), None)

                decision = self._decision(property_value, target_range)

                if self._should_log_property_status(
                    environment_id=environment_id,
                    property_name=property_name,
                    property_value=property_value,
                    target_range=target_range,
                    decision=decision,
                ):
                    self._log(
                        (
                            f"{property_name} is {property_value}; "
                            f"target min={target_range.get('min')} max={target_range.get('max')} -> {decision}"
                        ),
                        level="INFO",
                        entity=environment_id,
                    )

                await self._apply_device_commands(
                    environment_id=environment_id,
                    property_name=property_name,
                    decision=decision,
                    controllers=property_config.get("controllers", []),
                    devices=devices,
                    target_range=target_range,
                    property_value=property_value,
                )

    # ------------------------------------------------------------------
    # Decision helpers
    # ------------------------------------------------------------------
    def _aggregate_sensor_values(
        self,
        property_name: str,
        sensors: Iterable[str],
        sensor_data: Mapping[str, object],
    ) -> Optional[float]:
        """Return the average reading for a property across available sensors."""

        values: List[float] = []

        for sensor_id in sensors:
            if sensor_id not in sensor_data:
                continue

            reading = sensor_data.get(sensor_id)
            if isinstance(reading, dict):
                reading_value = reading.get(property_name)
            else:
                reading_value = reading

            if reading_value is None:
                continue

            try:
                values.append(float(reading_value))
            except (TypeError, ValueError):  # pragma: no cover - defensive casting
                self._log(
                    f"Sensor '{sensor_id}' returned non-numeric value for '{property_name}': {reading_value}",
                    level="WARNING",
                    entity=property_name,
                )

        if not values:
            return None

        return sum(values) / len(values)

    def _select_schedule(self, property_name: str, schedule_ids: Iterable[str]):
        """Choose the first active schedule for a property based on time range."""

        now = _dt.datetime.now().time()
        for schedule_id in schedule_ids:
            schedule = self.schedules.get(schedule_id)
            if not schedule:
                continue

            time_range = schedule.get("time_range")
            if time_range and not _time_in_range(time_range, now):
                continue

            if property_name not in schedule.get("targets", {}):
                continue

            return schedule

        return None

    def _decision(self, value: float, target_range: Mapping[str, object]) -> str:
        minimum = target_range.get("min")
        maximum = target_range.get("max")

        if minimum is not None and value < minimum:
            return "increase"
        if maximum is not None and value > maximum:
            return "decrease"
        return "stable"

    async def _apply_state_targets(
        self,
        *,
        environment_id: str,
        property_name: str,
        desired_state: str,
        controllers: Iterable[str],
        devices: Mapping[str, object],
    ) -> None:
        command = "turn_on" if desired_state == "on" else "turn_off"
        for device_id in controllers:
            history_key = (device_id, property_name)
            last_action, last_time = self._last_commands.get(history_key, (None, 0))
            now = time.monotonic()

            if last_action == command and now - last_time < self.state_refresh_seconds:
                self._log(
                    (
                        f"No state change for '{device_id}' controlling '{property_name}'; "
                        f"last {command} sent {now - last_time:.1f}s ago"
                    ),
                    level="DEBUG",
                    entity=environment_id,
                )
                continue

            device_effects = self._device_effects(device_id)
            if not device_effects:
                self._log(
                    f"No effects declared for device '{device_id}' controlling '{property_name}'",
                    level="WARNING",
                    entity=environment_id,
                )
                continue

            for effect in device_effects:
                if effect.get("property") != property_name:
                    continue

                command_issued = await self._issue_command(
                    device_id=device_id,
                    devices=devices,
                    command=command,
                    environment_id=environment_id,
                    property_name=property_name,
                    property_value=desired_state,
                    target_range={"state": desired_state},
                )

                if command_issued:
                    self._log(
                        f"Setting {property_name} to {desired_state} via {command}",
                        level="INFO",
                        entity=environment_id,
                    )

    async def _apply_device_commands(
        self,
        *,
        environment_id: str,
        property_name: str,
        decision: str,
        controllers: Iterable[str],
        devices: Mapping[str, object],
        target_range: Mapping[str, object],
        property_value: float,
    ) -> None:
        for device_id in controllers:
            device_effects = self._device_effects(device_id)
            if not device_effects:
                self._log(
                    f"No effects declared for device '{device_id}' controlling '{property_name}'",
                    level="WARNING",
                    entity=environment_id,
                )
                continue

            for effect in device_effects:
                if effect.get("property") != property_name:
                    continue

                command = self._determine_command(decision, effect)
                if not command:
                    continue

                await self._issue_command(
                    device_id=device_id,
                    devices=devices,
                    command=command,
                    environment_id=environment_id,
                    property_name=property_name,
                    property_value=property_value,
                    target_range=target_range,
                )

    def _determine_command(self, decision: str, effect: Mapping[str, object]) -> Optional[str]:
        """
        Convert a decision ('increase' / 'decrease' / 'stable') into a device command
        using the effect's 'policy' mapping.

        Assumptions (enforced by _validate_configuration):
        - 'policy' exists and is a Mapping.
        - It defines all required decisions ('increase', 'decrease', 'stable').
        """
        policy = effect.get("policy")
        # Under correct config this should never happen; _validate_configuration enforces it.
        if not isinstance(policy, Mapping):
            raise RuntimeError(
                f"Effect for property '{effect.get('property')}' is missing a 'policy' mapping."
            )

        desired = str(policy[decision]).lower()  # safe: all decisions are required

        if desired == "on":
            return "turn_on"
        if desired == "off":
            return "turn_off"

        # Anything else ('ignore', 'none', etc.) means "no command for this decision"
        return None

    async def _issue_command(
        self,
        *,
        device_id: str,
        devices: Mapping[str, object],
        command: str,
        environment_id: str,
        property_name: str,
        property_value: float,
        target_range: Mapping[str, object],
    ) -> bool:
        now = time.monotonic()
        history_key = (device_id, property_name)
        last_action, last_time = self._last_commands.get(history_key, (None, 0))

        # Debounce and state-refresh behavior based on last command of this type
        if last_action == command:
            if now - last_time < self.debounce_seconds:
                self._log(
                    (
                        f"Skipping {command} for '{device_id}' controlling '{property_name}' "
                        f"due to debounce window ({self.debounce_seconds}s)"
                    ),
                    level="DEBUG",
                    entity=environment_id,
                )
                return False

            if now - last_time < self.state_refresh_seconds:
                self._log(
                    (
                        f"No state change for '{device_id}' controlling '{property_name}'; "
                        f"last {command} sent {now - last_time:.1f}s ago"
                    ),
                    level="DEBUG",
                    entity=environment_id,
                )
                return False

        device = devices.get(device_id)
        if not device:
            self._log(
                f"Device '{device_id}' not found in registry; cannot send {command}.",
                level="ERROR",
                entity=environment_id,
            )
            return False

        command_fn = getattr(device, command, None)
        if not callable(command_fn):
            self._log(
                f"Device '{device_id}' does not implement '{command}'.",
                level="ERROR",
                entity=environment_id,
            )
            return False

        summary = (
            f"{command} {device_id} for {property_name} in {environment_id} "
            f"(value={property_value}, target={target_range})"
        )
        desired_state_label = "on" if command == "turn_on" else "off"

        if self.dry_run:
            self._log(f"[dry-run] Would {summary}", level="INFO", entity=environment_id)
            self._last_commands[history_key] = (command, now)
            return True

        try:
            result = command_fn()
            if isawaitable(result):
                result = await result

            command_sent = True
            if isinstance(result, PowerCommandResult):
                command_sent = result.command_sent
            elif hasattr(result, "command_sent"):
                command_sent = bool(getattr(result, "command_sent"))

            if command_sent:
                self._log(summary, level="INFO", entity=environment_id)
                self._last_commands[history_key] = (command, now)
            else:
                self._log(
                    (
                        f"No-op: {device_id} already {desired_state_label} "
                        f"for '{property_name}'"
                    ),
                    level="DEBUG",
                    entity=environment_id,
                )

            return command_sent
        except Exception as exc:  # pragma: no cover - defensive logging
            self._log(
                f"Failed to execute {command} on '{device_id}': {exc}",
                level="ERROR",
                entity=environment_id,
            )

        return False

    async def _device_power_state(
        self, *, device: object, device_id: str, environment_id: str
    ) -> Optional[bool]:
        """Return True/False when a device exposes a power state API."""

        state_fn = getattr(device, "is_on", None)
        if not callable(state_fn):
            return None

        try:
            state = state_fn()
            if isawaitable(state):
                state = await state
        except Exception as exc:  # pragma: no cover - defensive logging
            self._log(
                f"Unable to query power state for '{device_id}': {exc}",
                level="WARNING",
                entity=environment_id,
            )
            return None

        return bool(state) if state is not None else None

    def _device_effects(self, device_id: str) -> List[Mapping[str, object]]:
        definition = self.device_definitions.get(device_id, {})
        effects = list(definition.get("effects", []) or [])

        if effects:
            return effects

        default_effects = self.device_effect_defaults.get(definition.get("what"))
        if default_effects:
            return list(default_effects)

        return []

    def _should_log_property_status(
        self,
        *,
        environment_id: str,
        property_name: str,
        property_value: float,
        target_range: Mapping[str, object],
        decision: str,
    ) -> bool:
        """Only log property status when the value or decision changes."""

        status_key = (environment_id, property_name)
        rounded_value = round(property_value, 2)
        status = (
            rounded_value,
            decision,
            target_range.get("min"),
            target_range.get("max"),
        )

        if self._last_property_logs.get(status_key) == status:
            return False

        self._last_property_logs[status_key] = status
        return True

    def _log(self, message: str, *, level: str = "INFO", entity: str = "controller") -> None:
        if self._log_callback:
            self._log_callback(
                message,
                level=level,
                component_type="controller",
                entity_name=entity,
            )
            return

        logger.bind(COMPONENT_TYPE="controller", ENTITY_NAME=entity).log(level.upper(), message)


def _time_in_range(range_definition: str, now: _dt.time) -> bool:
    """Return True if the current time is within a HH:MM-HH:MM window."""

    try:
        start_str, end_str = range_definition.split("-")
        start = _to_time(start_str)
        end = _to_time(end_str)
    except (ValueError, TypeError):  # pragma: no cover - defensive parse
        return False

    if start <= end:
        return start <= now <= end

    # Over-midnight window
    return now >= start or now <= end


def _to_time(hhmm: str) -> _dt.time:
    hours, minutes = hhmm.split(":")
    return _dt.time(int(hours), int(minutes))
