"""KASA connection manager.

Handles discovery by device name, connection lifecycle, and async-to-sync
bridging. One connection per physical strip, shared across all plugs.

python-kasa is fully async. The daemon is synchronous. This module
bridges the gap with a dedicated event loop in a background thread,
similar to how the Govee sensor driver handles BLE.

KASA devices are identified by name (alias), not IP address. Names are
set in the KASA app and survive DHCP reassignment. Discovery finds
devices by broadcasting on the local network.

Usage:
    mgr = KasaConnectionManager()
    mgr.start()

    # Get a plug by strip name + plug name
    plug = mgr.get_plug("Shed Strip", "Heater")
    plug.turn_on()
    watts = plug.read_power()

    mgr.stop()
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

import kasa

log = logging.getLogger(__name__)


class KasaError(Exception):
    """Raised when a KASA operation fails."""
    pass


@dataclass
class _CachedDevice:
    """A discovered and connected KASA device."""
    device: kasa.Device
    alias: str
    last_update: float = 0.0
    # Map of child alias -> child Device for strips
    children: dict[str, kasa.Device] = field(default_factory=dict)


class KasaConnectionManager:
    """Manages connections to KASA devices.

    Discovers devices by name, maintains persistent connections,
    and provides synchronous wrappers around async python-kasa calls.

    Thread-safe: the async event loop runs in a background thread.
    All public methods submit work to that loop and block for results.
    """

    # How often to re-discover (seconds). Handles IP changes.
    REDISCOVERY_INTERVAL = 300

    # How often to call device.update() to refresh state (seconds).
    UPDATE_INTERVAL = 5

    def __init__(self, credentials: kasa.Credentials | None = None,
                 discovery_timeout: int = 10) -> None:
        self._credentials = credentials
        self._discovery_timeout = discovery_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._devices: dict[str, _CachedDevice] = {}  # alias -> cached
        self._lock = threading.Lock()
        self._last_discovery = 0.0
        self._started = False

    def start(self) -> None:
        """Start the background event loop thread."""
        if self._started:
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name='kasa-connection-manager',
            daemon=True,
        )
        self._thread.start()
        # Wait for loop to be ready
        deadline = time.time() + 5.0
        while self._loop is None and time.time() < deadline:
            time.sleep(0.01)
        if self._loop is None:
            raise KasaError("Failed to start KASA event loop")
        self._started = True
        log.info("KASA connection manager started")

    def stop(self) -> None:
        """Stop the background event loop and disconnect all devices."""
        if not self._started:
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5.0)
        self._started = False
        log.info("KASA connection manager stopped")

    def _run_loop(self) -> None:
        """Background thread: run the async event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # Disconnect all devices
            for cached in self._devices.values():
                try:
                    self._loop.run_until_complete(
                        cached.device.disconnect()
                    )
                except Exception:
                    pass
            self._loop.close()

    def _run_async(self, coro):
        """Submit a coroutine to the background loop and wait for result."""
        if not self._loop or not self._loop.is_running():
            raise KasaError("KASA connection manager not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=15.0)
        except asyncio.TimeoutError:
            raise KasaError("KASA operation timed out")
        except kasa.KasaException as e:
            raise KasaError(f"KASA error: {e}") from e

    def discover(self) -> dict[str, kasa.Device]:
        """Run discovery and cache results. Returns alias -> Device map."""
        return self._run_async(self._async_discover())

    async def _async_discover(self) -> dict[str, kasa.Device]:
        """Discover all KASA devices on the network."""
        kwargs = {'discovery_timeout': self._discovery_timeout}
        if self._credentials:
            kwargs['credentials'] = self._credentials

        found = await kasa.Discover.discover(**kwargs)

        result = {}
        for host, device in found.items():
            try:
                await device.update()
            except Exception as e:
                log.warning("Failed to update %s at %s: %s",
                            getattr(device, 'alias', '?'), host, e)
                continue

            alias = device.alias
            cached = _CachedDevice(
                device=device,
                alias=alias,
                last_update=time.time(),
            )

            # Index children (strip plugs) by their alias
            for child in device.children:
                cached.children[child.alias] = child

            with self._lock:
                self._devices[alias] = cached
            result[alias] = device

        self._last_discovery = time.time()
        log.info("KASA discovery found %d device(s): %s",
                 len(result), ', '.join(result.keys()))
        return result

    def _ensure_discovered(self) -> None:
        """Run discovery if we haven't recently."""
        now = time.time()
        if now - self._last_discovery > self.REDISCOVERY_INTERVAL:
            self.discover()

    def get_strip(self, strip_name: str) -> kasa.Device:
        """Get a strip device by alias name.

        Args:
            strip_name: The alias set in the KASA app.

        Returns:
            The kasa.Device for the strip.

        Raises:
            KasaError: If the strip is not found.
        """
        self._ensure_discovered()
        with self._lock:
            cached = self._devices.get(strip_name)
        if cached is None:
            # Try one more discovery
            self.discover()
            with self._lock:
                cached = self._devices.get(strip_name)
        if cached is None:
            available = list(self._devices.keys())
            raise KasaError(
                f"KASA device '{strip_name}' not found. "
                f"Available: {available}"
            )
        return cached.device

    def get_plug(self, strip_name: str, plug_name: str) -> kasa.Device:
        """Get a specific plug on a strip by name.

        For a standalone plug (not a strip), strip_name is the plug's
        alias and plug_name should be the same or omitted.

        Args:
            strip_name: The alias of the strip/plug.
            plug_name: The alias of the specific child plug.

        Returns:
            The kasa.Device for the plug (child device on a strip,
            or the device itself for standalone plugs).

        Raises:
            KasaError: If the strip or plug is not found.
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

        # Standalone plug — no children
        if not cached.children:
            return cached.device

        # Strip — look up child by alias
        child = cached.children.get(plug_name)
        if child is None:
            available = list(cached.children.keys())
            raise KasaError(
                f"Plug '{plug_name}' not found on '{strip_name}'. "
                f"Available: {available}"
            )
        return child

    def update_device(self, device: kasa.Device) -> None:
        """Refresh device state from hardware.

        For strip children, we must update the parent strip since
        python-kasa children don't update independently.
        """
        target = device
        # If this is a child of a strip, update the parent
        if hasattr(device, 'parent') and device.parent is not None:
            target = device.parent
        self._run_async(target.update())

    def turn_on(self, device: kasa.Device) -> None:
        """Turn a device/plug on and verify."""
        self._run_async(device.turn_on())
        # Refresh state to confirm
        self.update_device(device)

    def turn_off(self, device: kasa.Device) -> None:
        """Turn a device/plug off and verify."""
        self._run_async(device.turn_off())
        # Refresh state to confirm
        self.update_device(device)

    def read_power(self, device: kasa.Device) -> float | None:
        """Read current power consumption in watts from a device/plug.

        Returns None if the device doesn't support energy monitoring.
        """
        if not device.has_emeter:
            return None
        self._run_async(device.update())
        energy = device.modules.get(kasa.Module.Energy)
        if energy is None:
            return None
        return energy.current_consumption

    def is_on(self, device: kasa.Device) -> bool:
        """Check if a device/plug is currently on."""
        return device.is_on

    def set_countdown(self, device: kasa.Device, seconds: int,
                      target_state: str = 'off') -> bool:
        """Set a hardware countdown timer on a device/plug.

        Args:
            device: The KASA device (plug or child).
            seconds: Delay in seconds.
            target_state: 'on' or 'off'.

        Returns:
            True if the countdown was set successfully.
        """
        countdown = device.modules.get(kasa.Module.IotCountdown)
        if countdown is None:
            return False

        act = 1 if target_state == 'on' else 0

        async def _set():
            # Clear existing rules first (only one allowed)
            rules = countdown.rules
            for rule in rules:
                await countdown.delete_rule(rule)
            # Add new countdown rule
            await countdown.call(
                'add_rule',
                {'enable': 1, 'delay': seconds, 'act': act,
                 'name': 'spriggler_safety'}
            )

        self._run_async(_set())
        return True

    def has_countdown(self, device: kasa.Device) -> bool:
        """Check if a device supports countdown timers."""
        return device.modules.get(kasa.Module.IotCountdown) is not None


# ── Singleton ────────────────────────────────────────────────────────

_manager: KasaConnectionManager | None = None
_manager_lock = threading.Lock()


def get_kasa_manager(
        credentials: kasa.Credentials | None = None,
        discovery_timeout: int = 10,
) -> KasaConnectionManager:
    """Get or create the singleton KASA connection manager.

    The manager is shared across all KASA device drivers and power
    sensors. One background thread, one discovery cache, one set of
    persistent connections.
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = KasaConnectionManager(
                credentials=credentials,
                discovery_timeout=discovery_timeout,
            )
            _manager.start()
        return _manager


def shutdown_kasa_manager() -> None:
    """Shut down the singleton manager. Called on daemon exit."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.stop()
            _manager = None