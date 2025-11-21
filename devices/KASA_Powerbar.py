"""Control integration for TP-Link KASA power strips."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

try:  # pragma: no cover - import is validated during unit tests
    from kasa import Discover, SmartStrip
except ImportError as exc:  # pragma: no cover - surfaced during initialization
    raise ImportError(
        "The 'python-kasa' package is required to use the KASA_Powerbar device"
    ) from exc


DEFAULT_KASA_PORT = 9999


def get_metadata() -> Dict[str, Any]:
    """Return module metadata used by dynamic documentation helpers."""

    return {
        "model": "KASA_Powerbar",
        "description": "Controls individual outlets on TP-Link KASA smart power strips.",
        "configuration": {
            "control": {
                "name": "Friendly device name configured in the KASA app (optional).",
                "outlet_name": "Outlet alias to control on the power strip.",
                "ip_address": "Static IP address used when discovery by name is not available.",
                "port": f"Optional TCP port (default {DEFAULT_KASA_PORT}).",
            }
        },
    }


class KasaPowerbar:
    """Interface to manage TP-Link KASA smart power strips."""

    # Cache of SmartStrip instances keyed by IP address (or discovered name).
    _strip_cache: Dict[str, SmartStrip] = {}

    def __init__(self, config: Dict[str, Any]):
        self.id = config.get("id", "kasa_powerbar")
        self.what = config.get("what", "power_device")
        self._config = dict(config)

        control = config.get("control") or {}
        if not control:
            raise ValueError("KASA_Powerbar requires a 'control' configuration block")

        self.device_name: Optional[str] = control.get("name")
        self.outlet_name: Optional[str] = control.get("outlet_name")
        self.ip_address: Optional[str] = control.get("ip_address") or config.get("address")
        self.port: int = int(control.get("port", DEFAULT_KASA_PORT))

        if not self.outlet_name:
            raise ValueError("KASA_Powerbar requires 'control.outlet_name'")

        if not (self.device_name or self.ip_address):
            raise ValueError(
                "KASA_Powerbar requires either 'control.name' or 'control.ip_address'"
            )

        power = config.get("power", {})
        self.power_rating = power.get("rating")
        self.circuit = power.get("circuit")

        self._strip: Optional[SmartStrip] = None
        self._outlet = None
        self.address: Optional[str] = None
        self._initialized = False

    def _cache_key(self) -> str:
        """Return a stable cache key for this power strip."""

        return self.ip_address or f"name::{self.device_name}"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    async def _discover_host(self) -> str:
        """Locate the power strip by its configured friendly name."""

        discovered = await Discover.discover()
        for host, device in discovered.items():
            alias = getattr(device, "alias", "")
            if alias and alias.lower() == self.device_name.lower():
                return host

        raise ValueError(
            f"Unable to locate KASA power strip named '{self.device_name}' via discovery"
        )

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("KASA_Powerbar has not been initialized")

    def _select_outlet(self) -> None:
        assert self._strip is not None  # noqa: S101 - defensive assertion
        candidates = [
            outlet
            for outlet in getattr(self._strip, "children", [])
            if getattr(outlet, "alias", "").lower() == self.outlet_name.lower()
        ]

        if not candidates:
            available = [getattr(child, "alias", "") for child in self._strip.children]
            raise ValueError(
                f"Outlet '{self.outlet_name}' was not found. Available outlets: {available}"
            )

        self._outlet = candidates[0]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """Initialize the underlying KASA device and outlet reference."""

        if self.device_name and not self.ip_address:
            self.ip_address = await self._discover_host()

        cache_key = self._cache_key()
        cached_strip = self._strip_cache.get(cache_key)

        if cached_strip:
            logger.bind(component="device", device_id=self.id).info(
                f"Reusing existing connection to KASA power strip at {self.ip_address} "
                f"(outlet '{self.outlet_name}')"
            )

            if hasattr(cached_strip, "protocol") and hasattr(cached_strip.protocol, "port"):
                cached_strip.protocol.port = self.port

            self._strip = cached_strip
            self.address = getattr(cached_strip, "host", self.ip_address)
        else:
            logger.bind(component="device", device_id=self.id).info(
                f"Connecting to KASA power strip at {self.ip_address} "
                f"(outlet '{self.outlet_name}')"
            )

            strip = SmartStrip(self.ip_address)

            if hasattr(strip, "protocol") and hasattr(strip.protocol, "port"):
                strip.protocol.port = self.port

            await strip.update()
            self._strip_cache[cache_key] = strip
            self._strip = strip
            self.address = strip.host

        self._select_outlet()
        self._initialized = True

        logger.bind(component="device", device_id=self.id).info(
            f"KASA outlet '{self.outlet_name}' is ready for commands."
        )

    def get_metadata(self) -> Dict[str, Any]:
        """Return descriptive metadata for the initialized outlet."""

        metadata: Dict[str, Any] = {
            "id": self.id,
            "what": self.what,
            "outlet": self.outlet_name,
            "circuit": self.circuit,
            "power_rating": self.power_rating,
        }

        if self._strip is not None:
            metadata.update(
                {
                    "host": getattr(self._strip, "host", self.ip_address),
                    "port": self.port,
                    "available_outlets": self._list_outlets(),
                }
            )
        else:
            metadata.update({"host": self.ip_address, "port": self.port})

        return metadata

    def _list_outlets(self) -> List[str]:
        assert self._strip is not None  # noqa: S101 - defensive assertion
        return [getattr(child, "alias", "") for child in getattr(self._strip, "children", [])]

    async def turn_on(self) -> None:
        """Turn on the configured outlet."""

        self._ensure_initialized()
        await self._outlet.turn_on()
        logger.debug("Issued ON command to %s", self.outlet_name)

    async def turn_off(self) -> None:
        """Turn off the configured outlet."""

        self._ensure_initialized()
        await self._outlet.turn_off()
        logger.debug("Issued OFF command to %s", self.outlet_name)

