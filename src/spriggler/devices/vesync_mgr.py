"""VeSync connection manager.

Handles login, session lifecycle, and device control for VeSync
cloud-controlled devices (Levoit humidifiers, etc.).

VeSync devices are cloud-only — all commands go through
smartapi.vesync.com.  There is no local control pathway.  If
internet is lost, the device continues in its last state until
the tank empties or connectivity resumes.

pyvesync is async (aiohttp).  This module bridges async → sync
with a background event loop, same pattern as the KASA manager.

Dependencies:
    pip install pyvesync
    (installed via: pip install spriggler[vesync])
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

log = logging.getLogger("spriggler.devices.vesync_mgr")


class VeSyncError(Exception):
    """Raised when a VeSync operation fails."""
    pass


class VeSyncConnectionManager:
    """Manages connection to VeSync cloud API.

    Thread-safe: async event loop runs in a background thread.
    All public methods submit work to that loop and block for results.
    """

    # How often to re-fetch device list from cloud
    UPDATE_INTERVAL = 60

    def __init__(self, email: str, password: str,
                 time_zone: str = "America/New_York") -> None:
        self._email = email
        self._password = password
        self._time_zone = time_zone
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._manager = None   # pyvesync.VeSync instance
        self._started = False
        self._logged_in = False
        self._last_update = 0.0

    def start(self) -> None:
        if self._started:
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="vesync-connection-manager",
            daemon=True,
        )
        self._thread.start()
        deadline = time.time() + 5.0
        while self._loop is None and time.time() < deadline:
            time.sleep(0.01)
        if self._loop is None:
            raise VeSyncError("Failed to start VeSync event loop")
        self._started = True

        # Login and discover
        self._run_async(self._async_login())
        log.info("VeSync connection manager started")

    def stop(self) -> None:
        if not self._started:
            return
        if self._loop and self._loop.is_running():
            if self._manager is not None:
                try:
                    self._run_async(self._async_close())
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5.0)
        self._started = False
        self._logged_in = False
        log.info("VeSync connection manager stopped")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            if self._manager is not None:
                try:
                    self._loop.run_until_complete(self._async_close())
                except Exception:
                    pass
            self._loop.close()

    def _run_async(self, coro, timeout: float = 30.0):
        """Submit a coroutine to the background loop and block."""
        if not self._loop or not self._loop.is_running():
            raise VeSyncError("VeSync connection manager not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            raise VeSyncError("VeSync operation timed out")
        except Exception as e:
            raise VeSyncError(f"VeSync error: {e}") from e

    async def _async_login(self) -> None:
        from pyvesync import VeSync

        self._manager = VeSync(
            self._email,
            self._password,
            time_zone=self._time_zone,
        )
        await self._manager.__aenter__()

        result = await self._manager.login()
        if not result or not self._manager.enabled:
            raise VeSyncError(
                "VeSync login failed. Check email/password."
            )

        await self._manager.update()
        self._logged_in = True
        self._last_update = time.time()

        humid_count = len(self._manager.devices.humidifiers)
        total = len(self._manager.devices)
        log.info("VeSync login OK: %d devices (%d humidifiers)",
                 total, humid_count)

        for h in self._manager.devices.humidifiers:
            log.info("  Humidifier: %s (%s)", h.device_name, h.device_type)

    async def _async_close(self) -> None:
        if self._manager is not None:
            try:
                await self._manager.__aexit__(None, None, None)
            except Exception:
                pass
            self._manager = None

    def _ensure_logged_in(self) -> None:
        if not self._started or not self._logged_in:
            raise VeSyncError("VeSync not connected")

    def _maybe_update(self) -> None:
        now = time.time()
        if now - self._last_update > self.UPDATE_INTERVAL:
            self._run_async(self._manager.update())
            self._last_update = now

    # ── Device lookup ────────────────────────────────────────────

    def get_humidifier(self, name: str):
        """Find a humidifier by device name (as set in VeSync app)."""
        self._ensure_logged_in()
        self._maybe_update()

        for h in self._manager.devices.humidifiers:
            if h.device_name == name:
                return h

        available = [h.device_name
                     for h in self._manager.devices.humidifiers]
        raise VeSyncError(
            f"Humidifier '{name}' not found. Available: {available}"
        )

    def list_humidifiers(self) -> list[dict]:
        """Return info about all discovered humidifiers for display."""
        self._ensure_logged_in()
        self._maybe_update()
        result = []
        for h in self._manager.devices.humidifiers:
            result.append({
                "name": h.device_name,
                "type": h.device_type,
                "is_on": h.is_on,
                "mode": getattr(h, "mode", "?"),
            })
        return result

    # ── Device control ───────────────────────────────────────────

    def turn_on(self, device) -> bool:
        """Turn on — fire and forget."""
        self._ensure_logged_in()
        try:
            self._run_async(device.turn_on())
            return True
        except VeSyncError:
            return False

    def turn_off(self, device) -> bool:
        """Turn off — fire and forget."""
        self._ensure_logged_in()
        try:
            self._run_async(device.turn_off())
            return True
        except VeSyncError:
            return False

    def set_mist_level(self, device, level: int) -> bool:
        """Set humidifier to manual mode at a specific mist level.

        Must set mode to 'manual' first, then set the level.
        Fire and forget — don't wait for confirmation.
        """
        self._ensure_logged_in()
        if level < 1:
            raise ValueError(f"Mist level must be positive, got {level}")
        try:
            self._run_async(device.set_mode("manual"))
            self._run_async(device.set_mist_level(level))
            return True
        except VeSyncError:
            return False

    def update_device(self, device) -> bool:
        """Refresh a single device's state from the cloud."""
        self._ensure_logged_in()
        try:
            self._run_async(device.update())
            return True
        except VeSyncError:
            return False

    def get_mist_level(self, device) -> int:
        """Get current mist level (0 = off)."""
        self._ensure_logged_in()
        try:
            self._run_async(device.update())
            return device.state.mist_virtual_level or 0
        except (VeSyncError, AttributeError):
            return 0


# ── Singleton ────────────────────────────────────────────────────

_manager: VeSyncConnectionManager | None = None
_manager_lock = threading.Lock()


def get_vesync_manager(
    email: str | None = None,
    password: str | None = None,
    time_zone: str = "America/New_York",
) -> VeSyncConnectionManager:
    """Get or create the singleton VeSync connection manager.

    First call requires email and password.  Subsequent calls
    return the existing manager.
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            if email is None or password is None:
                raise VeSyncError(
                    "VeSync email and password required on first call"
                )
            _manager = VeSyncConnectionManager(
                email=email,
                password=password,
                time_zone=time_zone,
            )
            _manager.start()
        return _manager


def shutdown_vesync_manager() -> None:
    """Shut down the singleton.  Called on daemon exit."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.stop()
            _manager = None
