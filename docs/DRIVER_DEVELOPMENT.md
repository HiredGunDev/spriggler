# Driver Development Guide

This document defines the contracts for implementing device and sensor drivers in Spriggler.

## Core Principle: Async-First

**All drivers must expose an async interface.** The Spriggler runtime is fully asynchronous. Drivers wrapping synchronous libraries must use `asyncio.to_thread()` internally.

```
┌─────────────────────────────────────────────────────────────┐
│  Spriggler Core (pure async)                                │
│  - spriggler.py                                             │
│  - environment_controller.py                                │
└─────────────────────────────────────────────────────────────┘
                            │
                            │  await driver.method()
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Drivers (all expose async interface)                       │
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────┐        │
│  │ Native Async Driver │    │ Wrapped Sync Driver │        │
│  │ (python-kasa)       │    │ (pyvesync)          │        │
│  │                     │    │                     │        │
│  │ async def turn_on() │    │ async def turn_on() │        │
│  │   await lib.on()    │    │   to_thread(sync)   │        │
│  └─────────────────────┘    └─────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

## Device Driver Contract

All device drivers must implement:

```python
class MyDevice:
    def __init__(self, config: dict):
        """Parse configuration. No I/O here."""
        self.id = config["id"]
        ...
    
    async def initialize(self) -> None:
        """Perform async initialization (connections, discovery)."""
        ...
    
    async def is_on(self) -> bool:
        """Query current power state."""
        ...
    
    async def turn_on(self) -> PowerCommandResult:
        """Turn the device on."""
        ...
    
    async def turn_off(self) -> PowerCommandResult:
        """Turn the device off."""
        ...
    
    def get_metadata(self) -> dict:
        """Return device metadata. This is the ONE sync method allowed."""
        ...
```

### Using `ensure_power_state`

```python
from devices.power_state import PowerCommandResult, ensure_power_state

async def turn_on(self) -> PowerCommandResult:
    return await ensure_power_state(
        desired_state=True,
        device_id=self.id,
        device_label=self.friendly_name,
        read_state=self.is_on,
        command=self._do_turn_on,
    )

async def _do_turn_on(self) -> None:
    await self._client.power_on()
```

## Sensor Driver Contract

All sensor drivers must implement:

```python
class MySensor:
    def __init__(self, config: dict):
        """Parse configuration. No I/O here."""
        ...
    
    async def initialize(self, logger) -> None:
        """Perform async initialization."""
        ...
    
    async def read(self) -> dict:
        """Read current sensor values."""
        ...
    
    async def stop_scanning(self) -> None:
        """Clean up resources."""
        ...
    
    def get_metadata(self) -> dict:
        """Return sensor metadata. This is the ONE sync method allowed."""
        ...
```

## Wrapping Synchronous Libraries

When the underlying library is synchronous (e.g., `pyvesync`), wrap blocking calls:

```python
import asyncio

class MySyncWrappedDevice:
    async def initialize(self) -> None:
        await asyncio.to_thread(self._sync_initialize)
    
    def _sync_initialize(self) -> None:
        self._client = SomeSyncLibrary(...)
        self._client.connect()
    
    async def is_on(self) -> bool:
        return await asyncio.to_thread(self._sync_is_on)
    
    def _sync_is_on(self) -> bool:
        self._client.update()
        return self._client.is_on
    
    async def turn_on(self) -> PowerCommandResult:
        return await ensure_power_state(
            desired_state=True,
            device_id=self.id,
            device_label=self.id,
            read_state=self.is_on,
            command=self._async_turn_on,
        )
    
    async def _async_turn_on(self) -> None:
        await asyncio.to_thread(self._client.turn_on)
```

## Key Points

1. **Wrap at the lowest level** — Individual library calls, not entire methods
2. **Keep sync helpers private** — Prefix with `_sync_` for clarity
3. **`to_thread` is cheap** — Don't over-optimize; clarity beats micro-performance
4. **No `isawaitable` checks** — The runtime assumes all drivers are async

## Checklist for New Drivers

- [ ] All public methods except `get_metadata()` are `async def`
- [ ] `__init__` performs no I/O
- [ ] Sync library calls wrapped with `asyncio.to_thread()`
- [ ] Uses `ensure_power_state` for power control (devices)
- [ ] Handles missing/invalid configuration gracefully
- [ ] Includes docstrings
