"""Control integration for VeSync-connected humidifiers.

The pyvesync library is synchronous, so this driver wraps all blocking
calls with asyncio.to_thread() to conform to the async-first architecture.
"""

from __future__ import annotations

import asyncio
from itertools import chain
from typing import Any, Dict, Iterable, Optional

from loguru import logger
from pyvesync import VeSync

from devices.power_state import PowerCommandResult, ensure_power_state


def get_metadata() -> Dict[str, Any]:
    """Return module metadata used by dynamic documentation helpers."""

    return {
        "model": "vesync_humidifier",
        "description": "Controls VeSync-compatible humidifiers such as Levoit models.",
        "configuration": {
            "control": {
                "name": "Friendly device name from the VeSync app.",
                "email": "VeSync account email used for authentication.",
                "password": "VeSync account password used for authentication.",
                "time_zone": "Optional IANA time zone (e.g. 'America/New_York').",
            },
            "power": {
                "circuit": "Optional circuit identifier for logging/metadata.",
                "rating": "Optional power rating (watts) for metadata only.",
            },
        },
    }


class VesyncHumidifier:
    """Interface to manage VeSync-compatible humidifiers."""

    def __init__(self, config: Dict[str, Any]):
        self.id = config.get("id", "vesync_humidifier")
        self.what = config.get("what", "humidifier")
        self._config = dict(config)

        control = config.get("control") or {}
        if not control:
            raise ValueError("VesyncHumidifier requires a 'control' configuration block")

        self.device_name: Optional[str] = control.get("name")
        self.email: Optional[str] = control.get("email") or control.get("username")
        self.password: Optional[str] = control.get("password")
        self.time_zone: Optional[str] = control.get("time_zone")

        if not self.device_name:
            raise ValueError("VesyncHumidifier requires 'control.name'")
        if not self.email or not self.password:
            raise ValueError("VesyncHumidifier requires 'control.email' and 'control.password'")

        power = config.get("power", {})
        self.power_rating = power.get("rating")
        self.circuit = power.get("circuit")

        self._manager: Optional[VeSync] = None
        self._device = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Sync helpers (called via to_thread)
    # ------------------------------------------------------------------
    def _sync_initialize(self) -> None:
        """Synchronous initialization — runs in thread pool."""
        self._manager = VeSync(
            self.email,
            self.password,
            time_zone=self.time_zone or "America/New_York",
        )

        if not self._manager.login():
            raise RuntimeError(f"VeSync login failed for account '{self.email}'")

        self._select_device()
        self._initialized = True

        logger.bind(component="device", device_id=self.id).info(
            f"VeSync humidifier '{self.device_name}' initialized successfully"
        )

    def _sync_is_on(self) -> bool:
        """Synchronous state check — runs in thread pool."""
        self._ensure_initialized()

        update = getattr(self._device, "update", None)
        if callable(update):
            update()

        status = getattr(self._device, "device_status", None)
        if isinstance(status, str):
            return status.lower() == "on"

        return bool(getattr(self._device, "is_on", False))

    def _sync_turn_on(self) -> None:
        """Synchronous turn on — runs in thread pool."""
        self._ensure_initialized()
        self._device.turn_on()

    def _sync_turn_off(self) -> None:
        """Synchronous turn off — runs in thread pool."""
        self._ensure_initialized()
        self._device.turn_off()

    # ------------------------------------------------------------------
    # Async public interface
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """Authenticate with VeSync and locate the configured humidifier."""
        await asyncio.to_thread(self._sync_initialize)

    async def is_on(self) -> bool:
        """Return True when the humidifier is running."""
        return await asyncio.to_thread(self._sync_is_on)

    async def turn_on(self) -> PowerCommandResult:
        """Turn on the humidifier."""
        return await ensure_power_state(
            desired_state=True,
            device_id=self.id,
            device_label=self.device_name or self.id,
            read_state=self.is_on,
            command=self._async_turn_on,
        )

    async def turn_off(self) -> PowerCommandResult:
        """Turn off the humidifier."""
        return await ensure_power_state(
            desired_state=False,
            device_id=self.id,
            device_label=self.device_name or self.id,
            read_state=self.is_on,
            command=self._async_turn_off,
        )

    async def _async_turn_on(self) -> None:
        """Async wrapper for sync turn_on."""
        await asyncio.to_thread(self._sync_turn_on)

    async def _async_turn_off(self) -> None:
        """Async wrapper for sync turn_off."""
        await asyncio.to_thread(self._sync_turn_off)

    def get_metadata(self) -> Dict[str, Any]:
        """Return descriptive metadata for the humidifier."""
        metadata: Dict[str, Any] = {
            "id": self.id,
            "what": self.what,
            "name": self.device_name,
            "circuit": self.circuit,
            "power_rating": self.power_rating,
        }

        if self._device is not None:
            metadata.update({
                "device_name": getattr(self._device, "device_name", self.device_name),
                "device_type": getattr(self._device, "device_type", None),
                "device_uuid": getattr(self._device, "uuid", None),
            })

        return metadata

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _ensure_initialized(self) -> None:
        if not self._initialized or self._device is None:
            raise RuntimeError("VesyncHumidifier has not been initialized")

    def _flatten_devices(self, dev_iterables: Iterable[Iterable[object]]) -> list[object]:
        """Return a flattened, deduplicated list of devices from iterables."""
        seen = set()
        devices: list[object] = []

        for device in chain.from_iterable(dev_iterables):
            identifier = (getattr(device, "uuid", None), getattr(device, "cid", None))
            if identifier in seen:
                continue
            seen.add(identifier)
            devices.append(device)

        return devices

    def _candidate_devices(self) -> list[object]:
        """Return all devices reported by VeSync that could be humidifiers."""
        assert self._manager is not None

        humidifiers = getattr(self._manager, "humidifiers", None)
        devices_mapping = getattr(self._manager, "devices", {})

        devices_from_mapping = []
        if isinstance(devices_mapping, dict):
            devices_from_mapping.extend(
                devices_mapping.get("humidifier") or devices_mapping.get("humidifiers") or []
            )

        additional_lists = []
        for attr in ("fans", "outlets", "switches", "bulbs", "scales", "motionsensors"):
            devs = getattr(self._manager, attr, None)
            if devs:
                additional_lists.append(devs)

        extra_from_dev_list = []
        dev_list = getattr(self._manager, "_dev_list", {})
        if isinstance(dev_list, dict):
            extra_from_dev_list.extend(dev_list.values())

        candidates = self._flatten_devices(
            [humidifiers or [], devices_from_mapping, *additional_lists, *extra_from_dev_list]
        )

        if not candidates:
            return []

        def looks_like_humidifier(device: object) -> bool:
            name = getattr(device, "device_name", "").lower()
            device_type = getattr(device, "device_type", "").lower()
            category = getattr(device, "device_category", "").lower()
            return "humid" in name or "humid" in device_type or "humid" in category

        humidifier_candidates = [d for d in candidates if looks_like_humidifier(d)]
        return humidifier_candidates or candidates

    def _select_device(self) -> None:
        assert self._manager is not None
        self._manager.update()

        humidifiers = self._candidate_devices()

        if not humidifiers:
            raise RuntimeError("Connected VeSync account does not report any humidifiers")

        matches = [
            device
            for device in humidifiers
            if getattr(device, "device_name", "").lower() == self.device_name.lower()
        ]

        if not matches:
            available = [getattr(device, "device_name", "") for device in humidifiers]
            raise ValueError(
                f"Humidifier '{self.device_name}' was not found. Available devices: {available}"
            )

        self._device = matches[0]
