"""KASA connection manager.

Handles discovery by device name, connection lifecycle, and async-to-sync
bridging.  One connection per physical device, shared across all drivers
that use that device.

python-kasa is fully async.  The daemon is synchronous.  This module
bridges the gap with a dedicated event loop in a background thread.

KASA devices are identified by name (alias), not IP address.  Names are
set in the KASA app and survive DHCP reassignment.

Dependencies:
    pip install python-kasa
    (installed via: pip install spriggler[kasa])
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("spriggler.devices.kasa_mgr")


class KasaError(Exception):
    """Raised when a KASA operation fails."""
    pass


@dataclass
class _CachedDevice:
    """A discovered and connected KASA device."""
    device: object          # kasa.Device
    alias: str
    last_update: float = 0.0
    children: dict[str, object] = field(default_factory=dict)


class KasaConnectionManager:
    """Manages connections to KASA devices.

    Thread-safe: async event loop runs in a background thread.
    All public methods submit work to that loop and block for results.
    """

    REDISCOVERY_INTERVAL = 300  # Re-discover every 5 min (handles IP changes)
    UPDATE_INTERVAL = 5         # Refresh device state every 5s

    def __init__(self, discovery_timeout: int = 10) -> None:
        self._discovery_timeout = discovery_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._devices: dict[str, _CachedDevice] = {}
        self._lock = threading.Lock()
        self._last_discovery = 0.0
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="kasa-connection-manager",
            daemon=True,
        )
        self._thread.start()
        deadline = time.time() + 5.0
        while self._loop is None and time.time() < deadline:
            time.sleep(0.01)
        if self._loop is None:
            raise KasaError("Failed to start KASA event loop")
        self._started = True
        log.info("KASA connection manager started")

    def stop(self) -> None:
        if not self._started:
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5.0)
        self._started = False
        log.info("KASA connection manager stopped")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # Disconnect all devices
            for cached in self._devices.values():
                try:
                    dev = cached.device
                    if hasattr(dev, "disconnect"):
                        self._loop.run_until_complete(dev.disconnect())
                except Exception:
                    pass
            self._loop.close()

    def _run_async(self, coro, timeout: float = 60.0):
        """Submit a coroutine to the background loop and block for result."""
        if not self._loop or not self._loop.is_running():
            raise KasaError("KASA connection manager not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            raise KasaError("KASA operation timed out")
        except Exception as e:
            raise KasaError(f"KASA error: {e}") from e

    # ── Discovery ────────────────────────────────────────────────

    def discover(self) -> dict[str, object]:
        """Discover all KASA devices on the local network.

        Returns dict of alias → kasa.Device.
        """
        # Bridge timeout must exceed discovery timeout
        bridge_timeout = self._discovery_timeout + 10
        return self._run_async(self._async_discover(), timeout=bridge_timeout)

    async def _async_discover(self) -> dict[str, object]:
        import kasa

        found = await kasa.Discover.discover(
            discovery_timeout=self._discovery_timeout,
        )

        result = {}
        for host, device in found.items():
            try:
                await device.update()
            except Exception as e:
                log.warning("Failed to update %s at %s: %s",
                            getattr(device, "alias", "?"), host, e)
                continue

            alias = device.alias
            cached = _CachedDevice(
                device=device,
                alias=alias,
                last_update=time.time(),
            )

            for child in device.children:
                cached.children[child.alias] = child

            with self._lock:
                self._devices[alias] = cached
            result[alias] = device

        self._last_discovery = time.time()
        log.info("KASA discovery: %d device(s) — %s",
                 len(result), ", ".join(result.keys()))
        return result

    def _ensure_discovered(self) -> None:
        if time.time() - self._last_discovery > self.REDISCOVERY_INTERVAL:
            self.discover()

    # ── Device/plug lookup ───────────────────────────────────────

    def get_plug(self, strip_name: str, plug_name: str) -> object:
        """Get a specific plug by strip alias and plug alias.

        For standalone plugs, strip_name == plug_name.
        """
        self._ensure_discovered()

        with self._lock:
            cached = self._devices.get(strip_name)
        if cached is None:
            self.discover()
            with self._lock:
                cached = self._devices.get(strip_name)
        if cached is None:
            available = list(self._devices.keys())
            raise KasaError(
                f"KASA device '{strip_name}' not found. "
                f"Available: {available}"
            )

        # Standalone plug
        if not cached.children:
            return cached.device

        # Strip — look up child
        child = cached.children.get(plug_name)
        if child is None:
            available = list(cached.children.keys())
            raise KasaError(
                f"Plug '{plug_name}' not found on '{strip_name}'. "
                f"Available: {available}"
            )
        return child

    def list_devices(self) -> list[dict]:
        """Return info about all discovered devices for display.

        Returns a list of dicts with keys: alias, model, type,
        is_strip, children, host.
        """
        self._ensure_discovered()
        result = []
        with self._lock:
            for alias, cached in self._devices.items():
                dev = cached.device
                info = {
                    "alias": alias,
                    "model": getattr(dev, "model", "?"),
                    "host": getattr(dev, "host", "?"),
                    "is_strip": len(cached.children) > 0,
                    "children": list(cached.children.keys()),
                }
                result.append(info)
        return result

    # ── Device control ───────────────────────────────────────────

    def turn_on(self, plug: object) -> None:
        self._run_async(plug.turn_on())

    def turn_off(self, plug: object) -> None:
        self._run_async(plug.turn_off())

    def update_device(self, plug: object) -> None:
        """Refresh device state from hardware."""
        target = plug
        if hasattr(plug, "parent") and plug.parent is not None:
            target = plug.parent
        self._run_async(target.update())

    def is_on(self, plug: object) -> bool:
        return plug.is_on

    def read_power(self, plug: object) -> float | None:
        """Read current power in watts.  Returns None if not supported."""
        import kasa
        if not plug.has_emeter:
            return None
        self.update_device(plug)
        energy = plug.modules.get(kasa.Module.Energy)
        if energy is None:
            return None
        return energy.current_consumption

    # ── Countdown timers ─────────────────────────────────────────

    def has_countdown(self, plug: object) -> bool:
        import kasa
        return plug.modules.get(kasa.Module.IotCountdown) is not None

    def set_countdown(self, plug: object, seconds: int,
                      target_state: str = "off") -> bool:
        import kasa

        countdown = plug.modules.get(kasa.Module.IotCountdown)
        if countdown is None:
            return False

        act = 1 if target_state == "on" else 0

        async def _set():
            for rule in countdown.rules:
                await countdown.delete_rule(rule)
            await countdown.call(
                "add_rule",
                {"enable": 1, "delay": seconds, "act": act,
                 "name": "spriggler_safety"},
            )

        self._run_async(_set())
        return True


# ── Singleton ────────────────────────────────────────────────────

_manager: KasaConnectionManager | None = None
_manager_lock = threading.Lock()


def get_kasa_manager(discovery_timeout: int = 10) -> KasaConnectionManager:
    """Get or create the singleton KASA connection manager."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = KasaConnectionManager(
                discovery_timeout=discovery_timeout,
            )
            _manager.start()
        return _manager


def shutdown_kasa_manager() -> None:
    """Shut down the singleton.  Called on daemon exit."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.stop()
            _manager = None
