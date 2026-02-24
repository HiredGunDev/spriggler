# Spriggler

Environmental control daemon for grow rooms, greenhouses, aquaculture, fermentation — any space where conditions matter.

## What It Is

Spriggler monitors sensors and controls devices to maintain target conditions across one or more environments. It doesn't use thresholds and rules. It uses physics.

During calibration, Spriggler measures what each device actually does to your specific space — how fast your heater raises temperature, how leaky your shed is, how your exhaust fan affects humidity. It derives physical constants (envelope conductance, device energy contributions) that remain valid across seasons and conditions. At runtime, a constrained optimizer solves for the best combination of device states to reach targets with minimum energy.

When things go wrong — and they will — Spriggler reasons about the situation the way a competent human would. If a heater fails in one chamber but there's excess heat in another, it discovers that running the inter-chamber fan is cheaper than panicking. If conditions are fine despite a device failure, it shrugs and moves on. A continuous cost function drives resources to wherever the situation is most dire, without static priority rankings.

## Key Features

- **Physics-based solver.** Not rules, not thresholds. Thermodynamics. The solver knows that heating air reduces humidity, that moving air between spaces transfers both heat and moisture, and that your circuit breaker has a limit.
- **Learns your setup.** Active calibration derives the R-value of your space and the energy contribution of each device in a few hours. No manual entry of watts, CFM, or guesswork. Recalibration happens passively during normal operation — model drift is treated as diagnostic information ("your insulation changed"), not a calibration problem.
- **Multi-environment optimization.** Manages multiple chambers simultaneously. Excess heat in one room warms another instead of running a heater. Failed device in one space triggers cross-environment resource sharing automatically.
- **Safety-first.** Independent safety monitor with veto authority over the solver. Hardware-level failsafe timers ensure devices enter safe states even if Spriggler crashes. Three layers of safety: hardware countdowns, safety monitor, solver constraints.
- **Domain-agnostic.** The solver knows nothing about plants, fish, or fermentation. It knows about temperatures, differentials, and cost functions. Your domain knowledge lives in the configuration — tight limits for fragile seedlings, wide limits for hardy specimens. The solver does the math.
- **Explainable.** Every decision traces to measured data and solved constraints. Structured JSON-lines logging means every cycle is auditable. "Why did you turn on the fan?" has a real answer.
- **Local-only.** All data stays on your device. No cloud, no accounts, no telemetry.

## Hardware

**Supported sensors:**
- Govee H5100/H5075 Bluetooth thermometer/hygrometer (via govee-ble + bleak)
    - Dual addressing: MAC address (Linux) or BLE name suffix (macOS)
    - Gateway filtering (H5151 hub advertisements ignored)

**Supported devices:**
- Mock driver for development and testing
- TP-Link KASA smart plugs and power strips *(driver in progress)*
- VeSync humidifiers *(driver planned)*

Drivers are modular with a defined contract and conformance test suite. See [Writing Drivers](docs/drivers.md) for adding hardware support.

## Installation

**Requirements:**
- Python 3.10+
- Bluetooth-capable host (Raspberry Pi recommended, macOS supported for development)

```bash
git clone https://github.com/your-repo/spriggler.git
cd spriggler
pip install -e .
```

## Quick Start

```bash
# 1. Describe your setup
cp config/example.json config/mysetup.json
nano config/mysetup.json

# 2. Start the daemon
spriggler-daemon --config config/mysetup.json

# 3. Watch it work
spriggler display

# 4. (future) Run calibration
spriggler calibrate --config config/mysetup.json

# 5. (future) Understand a decision
spriggler explain --cycle 42
```

## Architecture

Two executables, clean separation:

**`spriggler-daemon`** — the control loop. Runs forever, reads config, reads sensors, solves for optimal device states, writes status and logs. All output goes to a state directory (`~/.spriggler/` by default).

**`spriggler <command>`** — the CLI. Reads the state directory, never touches hardware.

| Command | Status | Description |
|---|---|---|
| `spriggler display` | ✅ Implemented | Live curses dashboard |
| `spriggler status` | Planned | One-shot status dump |
| `spriggler explain` | Planned | Explain solver decisions from logs |
| `spriggler calibrate` | Planned | Run calibration experiments |
| `spriggler check` | Planned | Validate config and exit |

No IPC between daemon and CLI. All coordination through the filesystem: `status.json` for current state, `spriggler.log` for structured event history. A UI crash cannot take down the daemon.

## State Directory

All daemon output lives in `~/.spriggler/` (override with `--state-dir` or `SPRIGGLER_STATE_DIR`):

| File | Description |
|---|---|
| `status.json` | Current state, written every cycle. Includes `running` flag for daemon health. |
| `spriggler.log` | Structured JSON-lines event log. Every sensor reading, solver decision, device command, safety event. |
| `calibration/` | *(future)* Learned physical constants per device and environment. |

## Package Structure

```
src/spriggler/
    cli/            CLI entry point and subcommands
      display.py    Live curses dashboard
    config/         Config loader with unit conversion
    sensors/        Sensor driver ABC, registry, implementations
      govee.py      Govee BLE driver (H5100/H5075)
      mock.py       Mock sensor for testing
    devices/        Device driver ABC, registry, implementations
      mock.py       Mock device for testing
    physics/        Thermodynamic model and predict functions
    safety/         Independent safety monitor with veto authority
    solver/         Constrained optimizer and cost functions
    daemon.py       Main control loop
    logging.py      Structured JSON-lines logger
    state.py        State directory resolution
    schedule.py     Time-based target and device override resolution
    units.py        Temperature unit conversion
```

## Documentation

- [Architecture](docs/architecture.md) — How it works, design decisions, and why
- [Configuration](docs/configuration.md) — Describing your setup
- [Calibration](docs/calibration.md) — Teaching the system your space *(coming soon)*
- [Drivers](docs/drivers.md) — Adding hardware support *(coming soon)*

## Project Status

**Version 0.3** — Active development. Core daemon loop, solver, safety monitor, config loader, Govee BLE sensor driver, structured logging, and live display are implemented and tested. Calibration, KASA device driver, and remaining CLI commands are in progress.

**300 tests passing** including driver conformance harness, safety monitor scenarios, solver validation, structured logging, and state directory resolution.

Version history: 0.1 was a prototype. 0.2 was a threshold-based controller that worked but was limited. 0.3 is a ground-up rewrite as a physics-based solver.

## License

MIT License. See LICENSE file.
