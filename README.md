# Spriggler

Environmental control daemon for grow rooms, greenhouses, aquaculture, fermentation — any space where conditions matter.

## What It Is

Spriggler monitors sensors and controls devices to maintain target conditions across one or more environments. It doesn't use thresholds and rules. It uses physics.

During calibration, Spriggler measures what each device actually does to your specific space — how fast your heater raises temperature, how leaky your tent is, how your exhaust fan affects humidity, how much thermal overshoot occurs after a heater shuts off. It derives physical constants (envelope conductance, device energy contributions, coast overshoot) that remain valid across seasons and conditions.

At runtime, a constrained optimizer solves for the best combination of device states to reach targets with minimum energy. The physics model predicts where each candidate combination will land — including thermal inertia effects — and the solver picks the lowest-cost option. The safety monitor has independent veto authority over every decision.

## Key Features

- **Physics-based solver.** Not rules, not thresholds. Thermodynamics. The solver knows that heating air reduces humidity, that moving air between spaces transfers both heat and moisture, and that your circuit breaker has a limit.
- **Coast compensation.** The model accounts for thermal inertia — a heater turned off at 76°F will coast to 80°F. The solver sees this and shuts off early, landing in the target zone instead of overshooting.
- **Learns your setup.** Active calibration derives the thermal time constant of your space and the energy contribution of each device in under an hour. No manual entry of watts, CFM, or guesswork.
- **Multi-environment optimization.** Manages multiple chambers simultaneously. Excess heat in one room warms another instead of running a heater. Failed device in one space triggers cross-environment resource sharing automatically.
- **Safety-first.** Independent safety monitor with veto authority over the solver. Hardware-level failsafe timers (KASA countdown) ensure devices enter safe states even if Spriggler crashes.
- **Domain-agnostic.** The solver knows nothing about plants, fish, or fermentation. It knows about temperatures, differentials, and cost functions. Your domain knowledge lives in the configuration.
- **Explainable.** Every decision traces to measured data and solved constraints. Structured JSON-lines logging means every cycle is auditable.
- **Local-first.** Sensor data and device control stay local wherever possible. VeSync humidifiers require cloud connectivity (no local API exists).

## Hardware

**Sensors:**
- Govee H5100/H5075 Bluetooth thermometer/hygrometer (via govee-ble + bleak)

**Devices:**
- TP-Link KASA smart plugs and power strips — on/off control with power monitoring, hardware countdown safety timers, fully local control
- Levoit VeSync humidifiers (Dual 200S tested) — graduated mist level control via VeSync cloud API
- Mock driver for development and testing

Drivers are modular with a defined contract and conformance test suite.

## Current Setup

Running on a Mac Mini controlling a seedling tent:

| Device | Driver | Role | Notes |
|---|---|---|---|
| HS300 power strip | kasa_plug | — | 3 outlets used |
| Ceramic heater (549W) | kasa_plug | heater | Coast overshoot: 4.3K / 7.7°F |
| LED grow light (40W) | kasa_plug | light | Net cooling effect (envelope loss > heat output) |
| USB exhaust fan (2.5W) | kasa_plug | exhaust | Transfers heat/humidity to ambient |
| Levoit Dual 200S | vesync_humidifier | humidifier | 2 mist levels, cloud-only control |
| Govee H5100 (×2) | govee_ble | sensor | Seedling + ambient temperature/humidity |

Envelope time constant: ~28 minutes (τ = 1668s). Calibrated conductance: 0.000599/s.

## Installation

**Requirements:**
- Python 3.11+
- Bluetooth-capable host (Raspberry Pi or Mac)
- pyvesync >= 3.0 (for VeSync humidifier support)

```bash
git clone <repo>
cd spriggler
pip install -e ".[dev]"
```

## Usage

```bash
# Calibrate (stop daemon first)
spriggler --home ~/Projects/spriggler calibrate all

# Run the daemon
python -m spriggler --home ~/Projects/spriggler

# Check status
spriggler --home ~/Projects/spriggler status
```

## Home Directory

All Spriggler files live under one root:

```
$SPRIGGLER_HOME/
├── config/
│   └── config.json         # User configuration
├── calibration/
│   ├── power.json          # Measured device wattage
│   ├── seedling_heater.json # Device thermal characterization
│   ├── seedling_light.json
│   ├── seedling_fan.json
│   └── envelope_seedling.json # Envelope thermal time constant
├── status.json             # Daemon heartbeat + current state
└── logs/
    └── spriggler.log       # Structured JSON-lines event log
```

## Architecture

Two entry points:

**`python -m spriggler`** — the daemon. Reads config, reads calibration data, runs the control loop (default 15s cycles), writes status and logs.

**`spriggler <command>`** — the CLI. `calibrate`, `status`, `check`.

No IPC between daemon and CLI. All coordination through the filesystem.

### Control Loop

Each cycle:
1. Read all sensors (Govee BLE)
2. Safety monitor evaluates — any absolute limits breached? Force safe states.
3. Resolve schedule targets for current time
4. Physics model predicts outcome of each candidate device combination
5. Solver picks lowest-cost combination (brute-force enumeration, ~4-12 combos)
6. Safety monitor reviews — veto if needed
7. Execute device commands (KASA local, VeSync cloud)
8. Log everything

### Physics Model

```
predicted = current - conductance × (current - ambient) + Σ device_contributions + coast_overshoot
```

Calibration files provide conductance (from envelope decay fits), device contributions (from active characterization), and coast overshoot (from thermal inertia measurement after device shutoff).

## Documentation

- [Architecture](docs/architecture.md) — Design principles, component details, data flow
- [Configuration](docs/configuration.md) — Config file reference
- [KASA Driver](docs/kasa_driver.md) — KASA hardware, firmware warnings, setup
- [VeSync Driver](docs/vesync_driver.md) — VeSync humidifier setup, cloud limitations

## Tests

```bash
python -m pytest tests/ -q
```

459 tests covering driver conformance, safety monitor scenarios, solver validation, physics model, calibration estimators, config loading, and structured logging.

## Project Status

**Version 0.3** — Physics solver with calibrated device models, coast compensation, and graduated device support. Running in production on a seedling tent.

## License

MIT