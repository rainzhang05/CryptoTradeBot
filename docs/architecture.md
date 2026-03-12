# Architecture Specification

## Architecture Goals

The system architecture must support:

- local-first development on macOS
- clean Docker execution
- easy later transition to cloud deployment
- shared logic between simulate and live modes
- a narrow, single-strategy implementation rather than a generic trading platform

## Primary Architectural Style

The project should use a modular monolith.

That means:

- one repository
- one deployable application image for normal runtime
- clear internal module boundaries
- shared domain models across research, backtest, simulate, and live execution

## Recommended Implementation Stack

The project should be implemented in Python.
This is the recommended baseline for all future implementation work unless the docs are explicitly changed.

### Why Python

- strong fit for market-data handling and research workflows
- strong ecosystem for backtesting and data processing
- suitable for CLI-first products
- easy Docker portability

## High-Level Components

The application must be organized into these major components.

### 1. Configuration subsystem

Responsibilities:

- load `.env` secrets
- load non-secret YAML settings
- validate runtime configuration
- expose a typed configuration object to the rest of the application

### 2. Data subsystem

Responsibilities:

- import raw Kraken historical datasets
- fetch incremental Kraken data from APIs
- fetch supplementary Binance and Coinbase data when needed for validation
- normalize data into canonical storage
- run data-integrity checks
- expose clean read interfaces for research and runtime

### 3. Feature subsystem

Responsibilities:

- compute deterministic features from canonical data
- cache reusable derived datasets
- ensure point-in-time correctness

Phase 3 stores derived datasets under `artifacts/features/<dataset_id>/` and may reserve
`artifacts/experiments/<dataset_id>/` for deterministic evaluation artifacts tied to that dataset.

### 4. Strategy subsystem

Responsibilities:

- apply rule-based eligibility and risk shell
- generate portfolio targets
- generate hold, reduce, exit, and freeze decisions

The strategy path is entirely deterministic.
It uses regime-aware exposure, feature-derived scoring, concentration caps, and freeze handling.
The checked-in runtime default is a hardened live preset, while the `max_profit` preset remains
available as an explicit override for research and backtest inspection.

### 5. Portfolio subsystem

Responsibilities:

- maintain position state
- maintain USD cash state
- compute realized and unrealized PnL
- generate target deltas between current and desired allocations

### 6. Execution subsystem

Responsibilities:

- translate target deltas into order intents
- execute live orders on Kraken
- simulate fills in simulate mode
- reconcile order and balance state
- detect execution anomalies and trigger freezes

Phase 7 implements this through a Kraken-focused live execution service that:

- authenticates private REST requests with API key, secret, and optional OTP
- refreshes Kraken's dead-man switch before each live decision cycle
- syncs USD cash, asset balances, and open orders before placing new orders
- reuses the shared order-intent builder from simulate and backtest mode
- persists restart-safe live state under `runtime/state/live_state.json`
- writes the latest operator-facing live status report under `artifacts/reports/runtime/latest_live_status.json`

### 7. Backtest subsystem

Responsibilities:

- replay historical data using strategy logic
- apply cost and execution assumptions
- generate trades, equity curves, and reports
- compare presets and evaluation windows

Phase 4 implements this as a deterministic daily bar engine that:

- consumes Phase 3 feature-store rows derived from canonical Kraken daily candles
- supports dataset-track overrides for deterministic evaluation
- generates target weights from the shared strategy path
- executes fills on the next aligned daily bar with configured fee and slippage assumptions
- writes `report.json`, `fills.csv`, `equity_curve.csv`, and `decisions.csv` under `artifacts/backtests/<run_id>/`
- maintains `artifacts/reports/backtests/latest_backtest_report.json` as the operator-friendly latest pointer

### 8. Runtime orchestration subsystem

Responsibilities:

- control long-running simulate and live sessions
- schedule daily strategy evaluations
- coordinate data refresh, decision, and execution
- emit monitoring events

### 9. Observability subsystem

Responsibilities:

- structured logging
- metrics and health state generation
- terminal monitoring output
- alert routing to email

### 10. CLI subsystem

Responsibilities:

- expose all operator workflows through short, consistent commands
- expose a full-screen interactive shell for operators on interactive terminals
- validate input and configuration
- dispatch to the correct application services
- normalize execution output into readable shell transcript events
- coordinate guided parameter collection, completion, and cancellation

Phase 8 implements the documented CLI surface through operator-oriented services that:

- validate Kraken connectivity through `tradebot doctor`
- expose tracked runtime status and managed-process stop control
- manage the configured alert email recipient and SMTP test flow
- expose the latest runtime context and alert history for operator inspection
- list and export stored reports and artifacts
- tail durable JSON log files from `runtime/logs/tradebot.log`

Phase 11 extends the CLI subsystem with:

- a shared command registry used by both direct CLI entrypoints and the interactive shell
- a Textual-based terminal shell with a centered header, vertically stacked operator context panels, a readable transcript, and a bottom input bar with inline suggestions
- guided field-selection and completion widgets for known-choice parameters
- a structured command-event layer that presents readable shell output without replacing durable logs
- cooperative cancellation support for long-running command execution

## Shared Domain Model Requirements

The project should define stable internal models for:

- asset identifiers
- market candles
- portfolio holdings
- balances
- orders
- fills
- signals
- trade decisions
- runtime health events

These models must be reused across backtest, simulate, and live pathways whenever possible.

## Runtime Modes

### Simulate mode

- uses the same strategy decision engine as live
- uses the same order-intent generation as live
- replaces real exchange execution with simulated fills
- records the same observability artifacts as live wherever practical

The current implementation reuses the backtest decision and simulated execution services for the latest available feature timestamp and persists portfolio state under `runtime/state/simulate_state.json`.
The default full-universe optimize-and-evaluate path uses `dynamic_universe_kraken_only`, while the live tradeable universe remains the fixed documented ten assets.

### Live mode

- uses the same strategy decision engine as simulate
- uses real Kraken account state and order placement
- emits terminal monitoring and email alerts
- freezes on critical integrity failures

Phase 7 delivers the shared live runtime loop, live account sync, and terminal monitoring output.
Phase 9 adds runtime alert routing, durable alert-deduplication state, and persisted runtime context for restart-safe diagnostics.

## State Management

The system must persist enough state to resume safely after restart.

### Required persisted state

- configuration snapshot used for the run
- current positions
- balances
- open orders
- recent fills
- latest strategy decisions
- runtime health and freeze state

Phase 4 also persists simulate-mode portfolio state so repeated local runs can resume from the last simulated holdings and cash balance.
Phase 7 also persists live-mode balances, holdings, open orders, fills, and freeze state so live runs can resume after restart with Kraken reconciliation.
Phase 8 also persists foreground runtime-process metadata under `runtime/state/runtime_process.json` so `tradebot status` and `tradebot stop` can inspect or manage an active runtime process.
Phase 9 also persists runtime context under `runtime/state/runtime_context.json`, alert-deduplication state under `runtime/state/alert_state.json`, and operator-facing mirrors under `artifacts/reports/runtime/`.
Phase 11 changes the default installed workspace from the repository root to a user application home rooted at `~/.tradebot/`, with `TRADEBOT_HOME` and `BOT_CONFIG_PATH` overrides for explicit workflows.

## Storage Layout Expectations

The implementation should separate:

- source code
- configuration
- raw data
- canonical data
- derived features
- runtime state
- reports and reproducible artifacts
