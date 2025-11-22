"""Control integration for VeSync-connected humidifiers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from itertools import chain

from loguru import logger
from pyvesync import VeSync


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

        assert self._manager is not None  # noqa: S101 - defensive assertion

        # Standard humidifier lists exposed by pyvesync
        humidifiers = getattr(self._manager, "humidifiers", None)
        devices_mapping = getattr(self._manager, "devices", {})

        devices_from_mapping = []
        if isinstance(devices_mapping, dict):
            devices_from_mapping.extend(
                devices_mapping.get("humidifier") or devices_mapping.get("humidifiers") or []
            )

        # Some builds categorize devices differently (e.g., fans); search all
        # known device collections for entries that smell like humidifiers.
        additional_lists = []
        for attr in (
            "fans",
            "outlets",
            "switches",
            "bulbs",
            "scales",
            "motionsensors",
        ):
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
        assert self._manager is not None  # noqa: S101 - defensive assertion
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Authenticate with VeSync and locate the configured humidifier."""

        self._manager = VeSync(self.email, self.password, time_zone=self.time_zone)

        if not self._manager.login():
            raise ValueError("Failed to log in to VeSync with the provided credentials")

        self._select_device()
        self._initialized = True

        logger.bind(component="device", device_id=self.id).info(
            f"VeSync humidifier '{self.device_name}' is ready for commands."
        )

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
            metadata.update(
                {
                    "device_name": getattr(self._device, "device_name", self.device_name),
                    "device_type": getattr(self._device, "device_type", None),
                    "device_uuid": getattr(self._device, "uuid", None),
                }
            )

        return metadata

    def turn_on(self) -> None:
        """Turn on the humidifier."""

        self._ensure_initialized()
        self._device.turn_on()
        logger.debug("Issued ON command to %s", self.device_name)

    def is_on(self) -> bool:
        """Return True when the humidifier is running."""

        self._ensure_initialized()

        update = getattr(self._device, "update", None)
        if callable(update):
            update()

        status = getattr(self._device, "device_status", None)
        if isinstance(status, str):
            return status.lower() == "on"

        return bool(getattr(self._device, "is_on", False))

    def turn_off(self) -> None:
        """Turn off the humidifier."""

        self._ensure_initialized()
        self._device.turn_off()
        logger.debug("Issued OFF command to %s", self.device_name)
