"""Common helpers for enforcing binary power states on devices.

All device drivers must expose an async interface. This module provides
a single async helper for power state management.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from loguru import logger


@dataclass
class PowerCommandResult:
    """Result of a power command attempt."""

    command_sent: bool
    """True if a command was actually sent to the device."""

    final_state: Optional[bool]
    """The verified state after the command, or None if unreadable."""


async def ensure_power_state(
    *,
    desired_state: bool,
    device_id: str,
    device_label: str,
    read_state: Optional[Callable[[], Awaitable[bool]]],
    command: Callable[[], Awaitable[None]],
) -> PowerCommandResult:
    """
    Apply an on/off command with pre-read and verification when supported.

    Args:
        desired_state: True for on, False for off.
        device_id: Unique identifier for logging context.
        device_label: Human-friendly name for log messages.
        read_state: Async function returning current power state, or None
                    if device doesn't support state queries.
        command: Async function that performs the actual power change.

    Returns:
        PowerCommandResult with command_sent and final_state fields.
    """
    bound_logger = logger.bind(component="device", device_id=device_id)

    pre_state = await _read_state(read_state, bound_logger, device_label)

    if pre_state is not None and pre_state == desired_state:
        bound_logger.debug(
            "No-op for '{}': already {}", device_label, _state_label(desired_state)
        )
        return PowerCommandResult(command_sent=False, final_state=pre_state)

    await command()

    post_state = await _verify_state(
        read_state=read_state,
        desired_state=desired_state,
        bound_logger=bound_logger,
        device_label=device_label,
        pre_state=pre_state,
    )

    return PowerCommandResult(command_sent=True, final_state=post_state)


def _state_label(state: bool) -> str:
    """Convert boolean state to human-readable label."""
    return "on" if state else "off"


async def _read_state(
    read_state: Optional[Callable[[], Awaitable[bool]]],
    bound_logger,
    device_label: str,
) -> Optional[bool]:
    """Read current power state if reader is available."""
    if read_state is None:
        return None

    try:
        state = await read_state()
        return bool(state) if state is not None else None
    except Exception as exc:
        bound_logger.warning(
            "Unable to read power state for '{}' before command: {}",
            device_label,
            exc,
        )
        return None


async def _verify_state(
    *,
    read_state: Optional[Callable[[], Awaitable[bool]]],
    desired_state: bool,
    bound_logger,
    device_label: str,
    pre_state: Optional[bool],
) -> Optional[bool]:
    """Verify state after command and log appropriately."""
    if read_state is None:
        return None

    try:
        post_state = await read_state()
        post_state = bool(post_state) if post_state is not None else None
    except Exception as exc:
        bound_logger.error(
            "Verification failed for '{}' after requesting {}: {}",
            device_label,
            _state_label(desired_state),
            exc,
        )
        return None

    if post_state is None:
        return None

    if post_state != desired_state:
        bound_logger.error(
            "Device '{}' did not reach desired state {} (actual: {})",
            device_label,
            _state_label(desired_state),
            _state_label(post_state),
        )
    elif pre_state is not None and post_state != pre_state:
        bound_logger.info(
            "Power state changed for '{}': {} -> {}",
            device_label,
            _state_label(pre_state),
            _state_label(post_state),
        )
    else:
        bound_logger.debug(
            "Power state for '{}' reported as {}",
            device_label,
            _state_label(post_state),
        )

    return post_state
