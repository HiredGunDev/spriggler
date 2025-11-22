"""Standalone safety timer tester for KASA powerbars.

Usage: python KASASafetyTest.py <path to config>

This script finds KASA powerbar devices in the provided Spriggler
configuration that declare safety settings. It forces a short (2 minute)
OFF safety timer on those outlets, powers them on, and then exits. The
powerbar's own scheduler should turn the outlets off after the timer
elapses.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from typing import Any, Dict, List

from loguru import logger

from config_loader import ConfigError, load_config
from devices.KASA_Powerbar import KasaPowerbar


SAFETY_OVERRIDE = {"target_state": "off", "timeout_minutes": 2, "enforce": True}


def _find_kasa_devices_with_safety(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    devices_block = config.get("devices", {})
    definitions = devices_block.get("definitions", [])

    matching: List[Dict[str, Any]] = []
    for device in definitions:
        if str(device.get("how", "")).lower() != "kasa_powerbar":
            continue

        control_block = device.get("control", {}) or {}
        if device.get("safety") or control_block.get("safety"):
            matching.append(device)

    return matching


async def _prime_outlet(device_config: Dict[str, Any]) -> None:
    config_copy = deepcopy(device_config)
    control_block = config_copy.setdefault("control", {})
    control_block["safety"] = dict(SAFETY_OVERRIDE)

    kasa_device = KasaPowerbar(config_copy)
    await kasa_device.initialize()
    await kasa_device.turn_on()

    logger.info(
        "Activated outlet '{}' on powerbar '{}' with a {} minute OFF safety timer",
        control_block.get("outlet_name"),
        control_block.get("name") or control_block.get("ip_address"),
        SAFETY_OVERRIDE["timeout_minutes"],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Turn on KASA outlets configured with safety rules and enforce a 2 minute OFF timer"
        )
    )
    parser.add_argument(
        "config_path",
        metavar="CONFIG",
        help="Path to a Spriggler configuration file (e.g., config/seedling.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        logger.error("Failed to load configuration: {}", exc)
        return 1

    kasa_devices = _find_kasa_devices_with_safety(config)
    if not kasa_devices:
        logger.warning("No KASA powerbar devices with safety settings were found in the config")
        return 0

    async def runner() -> None:
        for device in kasa_devices:
            try:
                await _prime_outlet(device)
            except Exception as exc:  # pragma: no cover - hardware interaction
                logger.error(
                    "Unable to program safety timer for device '{}': {}",
                    device.get("id"),
                    exc,
                )

    asyncio.run(runner())
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint behavior
    raise SystemExit(main())
