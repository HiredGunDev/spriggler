"""Common helpers for enforcing binary power states on devices.

All device drivers must expose an async interface. This module provides
a single async helper for power state management.

Verification of commanded state is handled by the control loop on subsequent
cycles, not immediately after command. This avoids race conditions with
physical device latency.
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

    desired_state: Optional[bool]
    """The state we commanded, for tracking by the control loop."""


async def ensure_power_state(
    *,
    desired_state: bool,
    device_id: str,
    device_label: str,
    read_state: Optional[Callable[[], Awaitable[bool]]],
    command: Callable[[], Awaitable[None]],
) -> PowerCommandResult:
    """
    Apply an on/off command with pre-read to avoid redundant commands.

    Verification is deferred to the next control loop cycle to handle
    physical device latency and manual interventions.

    Args:
        desired_state: True for on, False for off.
        device_id: Unique identifier for logging context.
        device_label: Human-friendly name for log messages.
        read_state: Async function returning current power state, or None
                    if device doesn't support state queries.
        command: Async function that performs the actual power change.

    Returns:
        PowerCommandResult with command_sent and desired_state fields.
    """
    bound_logger = logger.bind(component="device", device_id=device_id)

    pre_state = await _read_state(read_state, bound_logger, device_label)

    if pre_state is not None and pre_state == desired_state:
        bound_logger.debug(
            "No-op for '{}': already {}", device_label, _state_label(desired_state)
        )
        return PowerCommandResult(command_sent=False, desired_state=desired_state)

    await command()

    bound_logger.debug(
        "Command sent to '{}': {}", device_label, _state_label(desired_state)
    )

    return PowerCommandResult(command_sent=True, desired_state=desired_state)


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
