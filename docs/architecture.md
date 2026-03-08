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

This is the simplest architecture that still supports production quality, testing, and future evolution.

## Recommended Implementation Stack

The project should be implemented in Python.
This is the recommended baseline for all future implementation work unless the docs are explicitly changed.

### Why Python

- best fit for market-data handling and ML workflows
- strong ecosystem for backtesting, data processing, and modeling
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
- compute labels for training and evaluation
- cache reusable derived datasets
- ensure point-in-time correctness

Phase 3 stores derived datasets under `artifacts/features/<dataset_id>/` and reserves `artifacts/experiments/<dataset_id>/` for experiments that consume that dataset.

### 4. ML subsystem

Responsibilities:

- train predictive models
- validate models with walk-forward methods
- version model artifacts
- expose inference outputs to the strategy engine

Phase 6 implements this through a local artifact-oriented model service that:

- trains only on deterministic Phase 3 feature-store rows
- performs expanding walk-forward validation across later timestamps
- writes bundle, manifest, metrics, and prediction artifacts under `artifacts/models/<model_id>/`
- writes latest operator-facing summaries under `artifacts/reports/models/`
- maintains a promoted-model reference so runtime and backtests can load the active artifact deterministically

### 5. Strategy subsystem

Responsibilities:

- apply rule-based eligibility and risk shell
- consume ML outputs
- generate portfolio targets
- generate hold, reduce, exit, and freeze decisions

The implemented strategy path keeps the rule shell authoritative for hard vetoes, regime-aware exposure, and freeze handling, then blends optional promoted-model predictions into ranking, entry gating, and sell refinement.

### 6. Portfolio subsystem

Responsibilities:

- maintain position state
- maintain USD cash state
- compute realized and unrealized PnL
- generate target deltas between current and desired allocations

### 7. Execution subsystem

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

### 8. Backtest subsystem

Responsibilities:

- replay historical data using strategy logic
- apply cost and execution assumptions
- generate trades, equity curves, and reports
- compare baselines and candidate models

Phase 4 implements this as a deterministic daily bar engine that:

- consumes Phase 3 feature-store rows derived from canonical Kraken daily candles
- generates target weights from the shared strategy path
- executes fills on the next aligned daily bar with configured fee and slippage assumptions
- writes `report.json`, `fills.csv`, `equity_curve.csv`, and `decisions.csv` under `artifacts/backtests/<run_id>/`
- maintains `artifacts/reports/backtests/latest_backtest_report.json` as the operator-friendly latest pointer

Phase 6 extends the same engine to enrich feature rows with promoted-model predictions when the active model matches the dataset in use.

### 9. Runtime orchestration subsystem

Responsibilities:

- control long-running simulate and live sessions
- schedule daily strategy evaluations
- coordinate data refresh, inference, decision, and execution
- emit monitoring events

### 10. Observability subsystem

Responsibilities:

- structured logging
- metrics and health state generation
- terminal monitoring output
- alert routing to email

### 11. CLI subsystem

Responsibilities:

- expose all operator workflows through short, consistent commands
- validate input and configuration
- dispatch to the correct application services

Phase 8 implements the documented CLI surface through operator-oriented services that:

- validate Kraken connectivity through `bot doctor`
- expose tracked runtime status and managed-process stop control
- manage the configured alert email recipient and SMTP test flow
- list and export stored reports and artifacts
- tail durable JSON log files from `runtime/logs/tradebot.log`

## Shared Domain Model Requirements

The project should define stable internal models for:

- asset identifiers
- market candles
- portfolio holdings
- balances
- orders
- fills
- signals
- model predictions
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

### Live mode

- uses the same strategy decision engine as simulate
- uses real Kraken account state and order placement
- emits terminal monitoring and email alerts
- freezes on critical integrity failures

Phase 7 delivers the shared live runtime loop, live account sync, and terminal monitoring output.
Email delivery remains part of the later observability and operations work.

## State Management

The system must persist enough state to resume safely after restart.

### Required persisted state

- configuration snapshot used for the run
- current positions
- balances
- open orders
- recent fills
- latest strategy decisions
- latest predictions
- runtime health and freeze state

Phase 4 also persists simulate-mode portfolio state so repeated local runs can resume from the last simulated holdings and cash balance.
Phase 6 also persists promoted-model reference state so the same active artifact is reused consistently across simulate and backtest runs until a newer model is promoted.
Phase 7 also persists live-mode balances, holdings, open orders, fills, and freeze state so live runs can resume after restart with Kraken reconciliation.
Phase 8 also persists foreground runtime-process metadata under `runtime/state/runtime_process.json` so `bot status` and `bot stop` can inspect or manage an active runtime process.

## Storage Layout Expectations

The implementation should separate:

- source code
- configuration
- raw data
- canonical data
- derived features
- experiment manifests and outputs
- model artifacts
- backtest results
- runtime logs
- reports

Large data artifacts should stay out of version control while small manifests and metadata may be tracked where useful.

## Docker Requirements

The project must support Docker as the standard packaging format.

### Docker goals

- reproducible environment
- one-command local run path
- future easy cloud migration
- support for both simulate and live mode execution

### Docker scope

At minimum, Docker must support:

- application build
- test execution
- CLI command execution
- simulate mode runtime

Live mode support in Docker is also required before final production readiness.

## Security Boundaries

- API secrets must never be hardcoded.
- Secrets live in `.env` only.
- Logs must avoid leaking secrets.
- Email credentials, if needed, must be treated as secrets.
- The application must validate that it is connected to the intended Kraken environment and account before live trading begins.

## Cloud Portability Rule

Nothing in the architecture should assume permanent dependence on the local machine beyond file-path defaults and local operator convenience.
All runtime behavior should be portable to a containerized environment with mounted volumes and environment variables.
