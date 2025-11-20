"""Environment control loop for Spriggler."""

from __future__ import annotations

import datetime as _dt
import time
from inspect import isawaitable
from typing import Dict, Iterable, List, Mapping, Optional

from loguru import logger


class EnvironmentController:
    """Decide how actuators should respond to current sensor readings."""

    def __init__(
        self,
        *,
        config: Mapping,
        log_callback=None,
        debounce_seconds: float = 5.0,
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
        self.dry_run = dry_run
        self._log_callback = log_callback
        self._last_commands: Dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
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
                    self._log(
                        f"No readings available for property '{property_name}'",
                        level="WARNING",
                        entity=environment_id,
                    )
                    continue

                decision = self._decision(property_value, target_range)
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
        self._log(
            f"Setting {property_name} to {desired_state} via {command}",
            level="INFO",
            entity=environment_id,
        )

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

                await self._issue_command(
                    device_id=device_id,
                    devices=devices,
                    command=command,
                    environment_id=environment_id,
                    property_name=property_name,
                    property_value=desired_state,
                    target_range={"state": desired_state},
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

                command = self._determine_command(decision, effect.get("type"))
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

    def _determine_command(self, decision: str, effect_type: Optional[str]) -> Optional[str]:
        if decision == "stable":
            return "turn_off"

        if decision == "increase":
            if effect_type == "increase":
                return "turn_on"
            if effect_type == "decrease":
                return "turn_off"

        if decision == "decrease":
            if effect_type == "decrease":
                return "turn_on"
            if effect_type == "increase":
                return "turn_off"

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
    ) -> None:
        now = time.monotonic()
        last_action, last_time = self._last_commands.get(device_id, (None, 0))
        if last_action == command and now - last_time < self.debounce_seconds:
            self._log(
                f"Skipping {command} for '{device_id}' due to debounce window ({self.debounce_seconds}s)",
                level="DEBUG",
                entity=environment_id,
            )
            return

        device = devices.get(device_id)
        if not device:
            self._log(
                f"Device '{device_id}' not found in registry; cannot send {command}.",
                level="ERROR",
                entity=environment_id,
            )
            return

        command_fn = getattr(device, command, None)
        if not callable(command_fn):
            self._log(
                f"Device '{device_id}' does not implement '{command}'.",
                level="ERROR",
                entity=environment_id,
            )
            return

        summary = (
            f"{command} {device_id} for {property_name} in {environment_id} "
            f"(value={property_value}, target={target_range})"
        )

        if self.dry_run:
            self._log(f"[dry-run] Would {summary}", level="INFO", entity=environment_id)
            self._last_commands[device_id] = (command, now)
            return

        try:
            result = command_fn()
            if isawaitable(result):
                await result

            self._log(summary, level="INFO", entity=environment_id)
            self._last_commands[device_id] = (command, now)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._log(
                f"Failed to execute {command} on '{device_id}': {exc}",
                level="ERROR",
                entity=environment_id,
            )

    def _device_effects(self, device_id: str) -> List[Mapping[str, object]]:
        definition = self.device_definitions.get(device_id, {})
        effects = list(definition.get("effects", []) or [])

        if effects:
            return effects

        default_effects = self.device_effect_defaults.get(definition.get("what"))
        if default_effects:
            return list(default_effects)

        return []

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
