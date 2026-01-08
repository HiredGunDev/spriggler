# Spriggler

Environmental control daemon for grow rooms, greenhouses, and controlled environments.

## What It Is

Spriggler monitors sensors and controls devices (heaters, fans, humidifiers, lights) to maintain target conditions in one or more environments. Unlike simple threshold-based controllers, Spriggler uses a physics-based solver that learns how your specific equipment affects your specific space, then optimizes for target conditions with minimum energy usage.

## Key Features

- **Learns your setup.** Calibration mode measures what each device actually does - no manual entry of watts, CFM, or guesswork.
- **Physics-aware.** Understands that heating air changes humidity, that moving air between spaces affects both, that circuits have limits.
- **Multi-environment.** Manages multiple grow chambers, optimizing across all of them. Excess heat in one room can warm another instead of running a heater.
- **Energy-optimized.** Solves for the cheapest path to target conditions, not just the obvious one.
- **Explainable.** Every decision can be traced to measured data and solved constraints. No black box.
- **Local-only.** All data stays on your device. No cloud, no accounts, no telemetry. Your grow is your business.

## Hardware

**Supported sensors:**

- Govee H5100 Bluetooth thermometer/hygrometer

**Supported devices:**

- TP-Link KASA smart plugs and power strips (with energy monitoring)
- VeSync humidifiers

More drivers can be added. See `docs/drivers.md`.

## Installation

*Coming soon.*

## Quick Start

```bash
# 1. Describe your setup
nano config/spriggler.json

# 2. Run calibration (environment empty)
spriggler calibrate

# 3. Start the daemon
spriggler run

# 4. Check status
spriggler status

# Or with a custom config
spriggler run --config config/mysetup.json
```

## Documentation

- [Architecture](docs/architecture.md) - How it works
- [Configuration](docs/configuration.md) - Describing your setup
- [Calibration](docs/calibration.md) - Teaching the system
- [Drivers](docs/drivers.md) - Adding hardware support

## Project Status

Version 0.3 - Active development. Not yet ready for general use.

## License

MIT License. See LICENSE file.

