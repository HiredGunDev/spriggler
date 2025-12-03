"""Common helpers for enforcing binary power states on devices."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from typing import Awaitable, Callable, Optional

from loguru import logger


@dataclass
class PowerCommandResult:
    """Result of a power command attempt."""

    command_sent: bool
    final_state: Optional[bool]


async def ensure_power_state_async(
    *,
    desired_state: bool,
    device_id: str,
    device_label: str,
    read_state: Optional[Callable[[], Awaitable[Optional[bool]] | Optional[bool]]],
    command: Callable[[], Awaitable[None] | None],
) -> PowerCommandResult:
    """
    Apply an on/off command with pre-read and verification when supported.

    Returns a PowerCommandResult describing whether a command was sent and
    the final observed state (when readable).
    """

    bound_logger = logger.bind(component="device", device_id=device_id)
    pre_state = await _read_state_async(read_state, bound_logger, device_label)

    if pre_state is not None and pre_state == desired_state:
        bound_logger.debug(
            "No-op for '%s': already %s", device_label, _state_label(desired_state)
        )
        return PowerCommandResult(command_sent=False, final_state=pre_state)

    await command()
    post_state = await _verify_state_async(
        read_state=read_state,
        desired_state=desired_state,
        bound_logger=bound_logger,
        device_label=device_label,
        pre_state=pre_state,
    )
    return PowerCommandResult(command_sent=True, final_state=post_state)


def ensure_power_state(
    *,
    desired_state: bool,
    device_id: str,
    device_label: str,
    read_state: Optional[Callable[[], Optional[bool]]],
    command: Callable[[], None],
) -> PowerCommandResult:
    """
    Synchronous variant of ensure_power_state_async.
    """

    bound_logger = logger.bind(component="device", device_id=device_id)
    pre_state = _read_state_sync(read_state, bound_logger, device_label)

    if pre_state is not None and pre_state == desired_state:
        bound_logger.debug(
            "No-op for '%s': already %s", device_label, _state_label(desired_state)
        )
        return PowerCommandResult(command_sent=False, final_state=pre_state)

    command()
    post_state = _verify_state_sync(
        read_state=read_state,
        desired_state=desired_state,
        bound_logger=bound_logger,
        device_label=device_label,
        pre_state=pre_state,
    )
    return PowerCommandResult(command_sent=True, final_state=post_state)


def _state_label(state: bool) -> str:
    return "on" if state else "off"


async def _read_state_async(
    read_state: Optional[Callable[[], Awaitable[Optional[bool]] | Optional[bool]]],
    bound_logger,
    device_label: str,
) -> Optional[bool]:
    if read_state is None:
        return None

    try:
        state = read_state()
        if isawaitable(state):
            state = await state
        return bool(state) if state is not None else None
    except Exception as exc:  # pragma: no cover - defensive logging
        bound_logger.warning(
            "Unable to read power state for '%s' before command: %s", device_label, exc
        )
        return None


def _read_state_sync(
    read_state: Optional[Callable[[], Optional[bool]]], bound_logger, device_label: str
) -> Optional[bool]:
    if read_state is None:
        return None

    try:
        state = read_state()
        return bool(state) if state is not None else None
    except Exception as exc:  # pragma: no cover - defensive logging
        bound_logger.warning(
            "Unable to read power state for '%s' before command: %s", device_label, exc
        )
        return None


async def _verify_state_async(
    *,
    read_state: Optional[Callable[[], Awaitable[Optional[bool]] | Optional[bool]]],
    desired_state: bool,
    bound_logger,
    device_label: str,
    pre_state: Optional[bool],
) -> Optional[bool]:
    if read_state is None:
        return None

    try:
        post_state = read_state()
        if isawaitable(post_state):
            post_state = await post_state
    except Exception as exc:  # pragma: no cover - defensive logging
        bound_logger.error(
            "Verification failed for '%s' after requesting %s: %s",
            device_label,
            _state_label(desired_state),
            exc,
        )
        return None

    post_state_bool = bool(post_state) if post_state is not None else None
    _log_post_state(
        bound_logger=bound_logger,
        device_label=device_label,
        desired_state=desired_state,
        post_state=post_state_bool,
        pre_state=pre_state,
    )
    return post_state_bool


def _verify_state_sync(
    *,
    read_state: Optional[Callable[[], Optional[bool]]],
    desired_state: bool,
    bound_logger,
    device_label: str,
    pre_state: Optional[bool],
) -> Optional[bool]:
    if read_state is None:
        return None

    try:
        post_state = read_state()
    except Exception as exc:  # pragma: no cover - defensive logging
        bound_logger.error(
            "Verification failed for '%s' after requesting %s: %s",
            device_label,
            _state_label(desired_state),
            exc,
        )
        return None

    post_state_bool = bool(post_state) if post_state is not None else None
    _log_post_state(
        bound_logger=bound_logger,
        device_label=device_label,
        desired_state=desired_state,
        post_state=post_state_bool,
        pre_state=pre_state,
    )
    return post_state_bool


def _log_post_state(
    *,
    bound_logger,
    device_label: str,
    desired_state: bool,
    post_state: Optional[bool],
    pre_state: Optional[bool],
) -> None:
    if post_state is None:
        return

    if post_state != desired_state:
        bound_logger.error(
            "Device '%s' did not reach desired state %s (actual: %s)",
            device_label,
            _state_label(desired_state),
            _state_label(post_state),
        )
        return

    if pre_state is not None and post_state != pre_state:
        bound_logger.info(
            "Power state changed for '%s': %s -> %s",
            device_label,
            _state_label(pre_state),
            _state_label(post_state),
        )
    else:
        bound_logger.debug(
            "Power state for '%s' reported as %s", device_label, _state_label(post_state)
        )
