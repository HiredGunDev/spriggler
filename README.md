# Spriggler

Environmental control daemon for grow rooms, greenhouses, aquaculture, fermentation — any space where conditions matter.

## What It Is

Spriggler monitors sensors and controls devices to maintain target conditions across one or more environments. It doesn't use thresholds and rules. It uses physics.

During calibration, Spriggler measures what each device actually does to your specific space — how fast your heater raises temperature, how leaky your shed is, how your exhaust fan affects humidity. It derives physical constants (envelope conductance, device energy contributions) that remain valid across seasons and conditions. At runtime, a constrained optimizer solves for the best combination of device states to reach targets with minimum energy.

When things go wrong — and they will — Spriggler reasons about the situation the way a competent human would. If a heater fails in one chamber but there's excess heat in another, it discovers that running the inter-chamber fan is cheaper than panicking. If conditions are fine despite a device failure, it shrugs and moves on. A continuous cost function drives resources to wherever the situation is most dire, without static priority rankings.

## Key Features

- **Physics-based solver.** Not rules, not thresholds. Thermodynamics. The solver knows that heating air reduces humidity, that moving air between spaces transfers both heat and moisture, and that your circuit breaker has a limit.
- **Learns your setup.** 48-hour calibration derives the R-value of your space and the energy contribution of each device. No manual entry of watts, CFM, or guesswork. Recalibration happens passively during normal operation — model drift is treated as diagnostic information ("your insulation changed"), not a calibration problem.
- **Multi-environment optimization.** Manages multiple chambers simultaneously. Excess heat in one room warms another instead of running a heater. Failed device in one space triggers cross-environment resource sharing automatically.
- **Safety-first.** Independent safety monitor with veto authority over the solver. Hardware-level failsafe timers ensure devices enter safe states even if Spriggler crashes. Three layers of safety: hardware countdowns, safety monitor, solver constraints.
- **Domain-agnostic.** The solver knows nothing about plants, fish, or fermentation. It knows about temperatures, differentials, and cost functions. Your domain knowledge lives in the configuration — tight limits for fragile seedlings, wide limits for hardy specimens. The solver does the math.
- **Explainable.** Every decision traces to measured data and solved constraints. "Why did you turn on the fan?" has a real answer.
- **Local-only.** All data stays on your device. No cloud, no accounts, no telemetry.

## Hardware

**Supported sensors:**
- Govee H5100/H5075 Bluetooth thermometer/hygrometer (via govee-ble + bleak)

**Supported devices:**
- TP-Link KASA smart plugs and power strips (with energy monitoring and hardware countdown timers)
- VeSync humidifiers (with graduated mist levels and humidity sensing)

Drivers are modular with a defined contract and conformance test suite. See [Writing Drivers](docs/drivers.md) for adding hardware support.

## Installation

*Coming soon.*

**Requirements:**
- Python 3.10+
- Bluetooth-capable host (Raspberry Pi recommended)
- PsychroLib, bleak, govee-ble, python-kasa

## Quick Start

```bash
# 1. Describe your setup
cp config/example.json config/mysetup.json
nano config/mysetup.json

# 2. Run calibration (environment empty, 48+ hours)
spriggler calibrate --config config/mysetup.json

# 3. Start the daemon
spriggler run --config config/mysetup.json

# 4. Check status
spriggler status

# 5. Understand a decision
spriggler explain
```

## Documentation

- [Architecture](docs/architecture.md) — How it works, design decisions, and why
- [Configuration](docs/configuration.md) — Describing your setup
- [Calibration](docs/calibration.md) — Teaching the system your space *(coming soon)*
- [Drivers](docs/drivers.md) — Adding hardware support *(coming soon)*

## Contributing

Spriggler uses test-driven development. The architecture defines clear interfaces at every boundary, and a driver conformance test harness validates that new drivers meet the contract. Write a driver, run the tests, submit a PR.

See [Architecture — Development Philosophy](docs/architecture.md#development-philosophy) for details.

## Project Status

**Version 0.3** — Active development. Architecture and configuration schema are defined. Implementation in progress. Not yet ready for general use.

Version history: 0.1 was a prototype. 0.2 was a threshold-based controller that worked but was limited. 0.3 is a ground-up rewrite as a physics-based solver.

## License

MIT License. See LICENSE file.
