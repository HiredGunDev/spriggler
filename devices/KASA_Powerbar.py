"""Control integration for TP-Link KASA power strips."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

try:  # pragma: no cover - import is validated during unit tests
    from kasa import Discover, Module
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The 'python-kasa' package is required to use the KASA_Powerbar device"
    ) from exc

from devices.power_state import PowerCommandResult, ensure_power_state


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
                "safety": {
                    "target_state": "Desired fallback state ('on' or 'off').",
                    "timeout_minutes": "Delay in minutes before applying the fallback state.",
                    "enforce": "If false, safety logic is disabled for the outlet.",
                    "outlets": "Optional mapping of outlet names to safety configs; overrides defaults.",
                },
                "outlets": "Optional per-outlet blocks that can each declare their own safety rules.",
            }
        },
    }


class KasaPowerbar:
    """Interface to manage a single outlet on a TP-Link KASA smart power strip."""

    # Cache of device instances keyed by IP/name to reuse TCP sessions.
    _device_cache: Dict[str, Any] = {}

    def __init__(self, config: Dict[str, Any]):
        self.id = config["id"]
        self.what = config.get("what", "power_device")
        self._config = dict(config)

        control = config["control"]
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

        safety_config = self._resolve_safety_config(control)
        self._safety_target_state: Optional[str] = safety_config.get("target_state")
        timeout_minutes = safety_config.get("timeout_minutes")
        self._safety_timeout_minutes: Optional[float] = (
            float(timeout_minutes) if timeout_minutes is not None else None
        )
        self._safety_enforce: bool = bool(safety_config.get("enforce", True))

        self._device: Any = None       # IotStrip / Device
        self._outlet: Any = None       # IotStripPlug / child device
        self.address: Optional[str] = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Core plumbing
    # ------------------------------------------------------------------
    def _cache_key(self) -> str:
        """Return a stable cache key for this power strip."""
        return self.ip_address or f"name::{self.device_name}"

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
        """Bind self._outlet to the configured child alias."""
        assert self._device is not None  # defensive

        children = getattr(self._device, "children", [])
        candidates = [
            outlet
            for outlet in children
            if getattr(outlet, "alias", "").lower() == self.outlet_name.lower()
        ]

        if not candidates:
            available = [getattr(child, "alias", "") for child in children]
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
        cached = self._device_cache.get(cache_key)

        if cached is not None:
            dev = cached
            logger.bind(component="device", device_id=self.id).info(
                f"Reusing existing connection to KASA power strip at {self.ip_address} "
                f"(outlet '{self.outlet_name}')"
            )
        else:
            logger.bind(component="device", device_id=self.id).info(
                f"Connecting to KASA power strip at {self.ip_address} "
                f"(outlet '{self.outlet_name}')"
            )
            dev = await Discover.discover_single(self.ip_address)
            # proto may exist; honor configured port if we can
            if hasattr(dev, "protocol") and hasattr(dev.protocol, "port"):
                dev.protocol.port = self.port
            await dev.update()
            self._device_cache[cache_key] = dev

        self._device = dev
        self.address = getattr(dev, "host", self.ip_address)
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
            "safety": {
                "scope": "outlet",
                "target_state": self._safety_target_state,
                "timeout_minutes": self._safety_timeout_minutes,
                "enforce": self._safety_enforce,
            },
        }

        if self._device is not None:
            metadata.update(
                {
                    "host": getattr(self._device, "host", self.ip_address),
                    "port": self.port,
                    "available_outlets": self._list_outlets(),
                }
            )
        else:
            metadata.update({"host": self.ip_address, "port": self.port})

        return metadata

    def _list_outlets(self) -> List[str]:
        assert self._device is not None  # defensive
        return [getattr(child, "alias", "") for child in getattr(self._device, "children", [])]

    async def is_on(self) -> bool:
        """Return True when the configured outlet is powered on."""
        self._ensure_initialized()
        await self._device.update()
        return bool(getattr(self._outlet, "is_on", False))

    async def _set_power_state(self, *, desired_state: bool) -> PowerCommandResult:
        self._ensure_initialized()

        async def _command() -> None:
            if desired_state:
                await self._outlet.turn_on()
            else:
                await self._outlet.turn_off()

        result = await ensure_power_state(
            desired_state=desired_state,
            device_id=self.id,
            device_label=self.outlet_name or self.id,
            read_state=self.is_on,
            command=_command,
        )

        await self._apply_safety_programming(command_state=desired_state)
        return result

    async def turn_on(self) -> PowerCommandResult:
        """Turn on the configured outlet."""

        return await self._set_power_state(desired_state=True)

    async def turn_off(self) -> PowerCommandResult:
        """Turn off the configured outlet."""

        return await self._set_power_state(desired_state=False)

    # ------------------------------------------------------------------
    # Safety configuration
    # ------------------------------------------------------------------
    def _resolve_safety_config(self, control: Dict[str, Any]) -> Dict[str, Any]:
        """Return outlet-specific safety configuration if present."""

        # 1) Per-outlet block under control.outlets
        outlet_specific = self._extract_outlet_config(control)
        if isinstance(outlet_specific, dict):
            safety = outlet_specific.get("safety")
            if isinstance(safety, dict):
                return safety

        # 2) control.safety, optionally with per-outlet overrides
        control_safety = control.get("safety")
        if isinstance(control_safety, dict):
            outlets_block = control_safety.get("outlets")
            outlet_override = self._match_outlet_from_block(outlets_block)
            if outlet_override is not None:
                return outlet_override

            if self.outlet_name in control_safety and isinstance(
                    control_safety.get(self.outlet_name), dict
            ):
                return control_safety[self.outlet_name]

            return control_safety

        return {}

    def _extract_outlet_config(self, control: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        outlets = control.get("outlets")

        if isinstance(outlets, dict):
            candidate = outlets.get(self.outlet_name)
            if isinstance(candidate, dict):
                return candidate

        if isinstance(outlets, list):
            for entry in outlets:
                if (
                        isinstance(entry, dict)
                        and entry.get("outlet_name") == self.outlet_name
                ):
                    return entry

        return None

    def _match_outlet_from_block(self, outlets_block: Any) -> Optional[Dict[str, Any]]:
        if isinstance(outlets_block, dict):
            candidate = outlets_block.get(self.outlet_name)
            if isinstance(candidate, dict):
                return candidate

        if isinstance(outlets_block, list):
            for entry in outlets_block:
                if (
                        isinstance(entry, dict)
                        and entry.get("outlet_name") == self.outlet_name
                ):
                    return entry

        return None

    def _safety_settings(self) -> Tuple[Optional[bool], Optional[int], bool]:
        """Return parsed safety target, timeout (seconds), and enforce flag."""
        if not self._safety_enforce:
            return None, None, False

        if not self._safety_target_state:
            return None, None, False

        normalized = str(self._safety_target_state).lower()
        if normalized not in {"on", "off"}:
            logger.warning(
                "Invalid safety target_state '%s' - expected 'on' or 'off'",
                self._safety_target_state,
            )
            return None, None, False

        if self._safety_timeout_minutes is None:
            logger.warning(
                "Safety target configured without a timeout for outlet '%s'",
                self.outlet_name,
            )
            return None, None, False

        if self._safety_timeout_minutes <= 0:
            return None, None, False

        return normalized == "on", int(self._safety_timeout_minutes * 60), True

    # ------------------------------------------------------------------
    # Countdown-based failsafe
    # ------------------------------------------------------------------
    def _countdown_module(self):
        """Return the countdown module when exposed by the outlet."""
        modules = getattr(self._outlet, "modules", None)
        if not modules:
            return None
        return modules.get(Module.IotCountdown)

    async def _clear_countdown_rules(self) -> bool:
        """Clear countdown rules via low-level helper when available."""
        module = self._countdown_module()
        if module is None:
            return False

        if hasattr(self._outlet, "_query_helper"):
            try:
                await self._outlet._query_helper("count_down", "delete_all_rules", {})
                logger.debug("Cleared countdown rules for outlet '%s'", self.outlet_name)
                return True
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Failed to clear countdown failsafe for outlet '%s': %s",
                    self.outlet_name,
                    exc,
                )
        return False

    async def _program_countdown_failsafe(
            self, target_state: bool, timeout_seconds: int
    ) -> bool:
        """Program the HS300 internal countdown timer."""
        module = self._countdown_module()
        if module is None:
            return False

        await self._clear_countdown_rules()

        params = {
            "delay": timeout_seconds,
            "act": 1 if target_state else 0,  # 1 = ON, 0 = OFF for plugs
            "enable": 1,
            "name": "spriggler_failsafe",
        }

        if hasattr(self._outlet, "_query_helper"):
            try:
                await self._outlet._query_helper("count_down", "add_rule", params)
                logger.debug(
                    "Programmed countdown failsafe to switch %s in %s seconds",
                    "on" if target_state else "off",
                    timeout_seconds,
                )
                return True
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Failed to program countdown failsafe for outlet '%s': %s",
                    self.outlet_name,
                    exc,
                )

        return False

    async def _apply_safety_programming(self, command_state: bool) -> None:
        """
        Program or clear safety timers based on configuration.

        Logic:
        - If safety disabled or invalid -> clear any prior failsafe.
        - If the commanded state equals the safety fallback state -> clear any failsafe.
        - Otherwise program a countdown that moves from command_state -> target_state.
        """
        target_state, timeout_seconds, enforce = self._safety_settings()

        if not enforce or target_state is None or timeout_seconds is None:
            await self._clear_safety_programming()
            return

        # If the requested state already matches the fallback state, no timer needed.
        if command_state == target_state:
            await self._clear_safety_programming()
            return

        # Try countdown-based failsafe; if it fails, log and leave outlet as-is.
        if await self._program_countdown_failsafe(target_state, timeout_seconds):
            return

        logger.warning(
            "KASA outlet '%s' does not expose a usable countdown failsafe; "
            "safety cannot be enforced",
            self.outlet_name,
        )

    async def _clear_safety_programming(self) -> None:
        """Attempt to clear any previously programmed safety timers."""
        if await self._clear_countdown_rules():
            logger.debug("Cleared safety programming for outlet '%s'", self.outlet_name)
