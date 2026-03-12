# Implementation Roadmap

This roadmap defines the complete implementation path from an empty repository to a production-grade Kraken spot trading bot.
Each phase has a fixed objective, explicit scope, and a required deliverable.
Work is complete only when the phase deliverable exists and satisfies the acceptance criteria described in the supporting specification documents.

## Phase 0: Authoritative Planning Baseline

### Objective

Create the project source of truth before application code begins.

### Includes

- Create the documentation set in `docs/`.
- Define project goals, non-goals, and constraints.
- Freeze the V1 trading universe, exchange scope, operating currency, and runtime modes.
- Define the rule-only strategy direction at a high level.
- Define testing, CI, operations, and release-quality expectations.
- Create root-level agent instructions for future coding sessions.

### Deliverable

- A complete `docs/` folder that fully specifies the project plan.
- A root `AGENTS.md` that instructs coding agents how to work in this repository.

## Phase 1: Project Skeleton and Tooling Foundation

### Objective

Establish the repository structure and the minimum engineering foundation required for safe development.

### Includes

- Define the implementation language and package layout.
- Create the Python project structure for application code, tests, scripts, and Docker assets.
- Add dependency management and lockfile strategy.
- Add baseline configuration loading with `.env` and non-secret YAML settings.
- Add structured logging foundation.
- Add the initial Docker image and local container workflow.
- Add baseline GitHub Actions CI for install, test, lint, type-check, and coverage.

### Deliverable

- A buildable project skeleton with repeatable local setup and working CI.

## Phase 2: Historical Data Platform

### Objective

Build the canonical data pipeline that powers research, backtesting, simulation, and live trading.

### Includes

- Ingest full historical Kraken market data dumps provided by the user.
- Add incremental Kraken API ingestion for dates not present in local historical dumps.
- Build a canonical schema for OHLCV and metadata.
- Add symbol mapping for the fixed 10-asset universe.
- Add integrity checks for missing candles, duplicate timestamps, and malformed rows.
- Add Binance and Coinbase collectors for gap detection and cross-checking only.
- Implement gap report generation and source-confidence rules.
- Add local storage layout and metadata manifests.

### Deliverable

- A reproducible local historical market dataset in the canonical project format, with integrity reports and gap diagnostics.

## Phase 3: Research and Feature Engineering Framework

### Objective

Provide a research layer that can generate deterministic feature datasets for the rule-only strategy.

### Includes

- Implement feature generation from Kraken canonical data.
- Implement rule-based indicators and regime variables.
- Implement supplementary cross-check features from Binance and Coinbase only where permitted by the data policy.
- Add feature stores or cached derived datasets for repeatable evaluation.
- Add artifact layout and deterministic dataset identity conventions.

### Deliverable

- A deterministic research pipeline that turns raw canonical market data into reproducible signal datasets for backtests, simulate mode, and live mode.

## Phase 4: Backtesting Engine and Simulation Core

### Objective

Build an execution-aware backtesting engine that evaluates the strategy on Kraken conditions only.

### Includes

- Event-driven or bar-driven backtest engine aligned with the project specification.
- Portfolio accounting in USD.
- Kraken-specific fees, lot sizes, and execution constraints where historical metadata permits.
- Slippage and liquidity modeling with conservative assumptions.
- Trade journal, fills ledger, equity curve, and performance report generation.
- Simulation mode runtime that reuses the same strategy code path as live trading wherever practical.
- Regression fixtures to keep simulate and live behavior aligned.

### Deliverable

- A complete backtesting engine and a simulation runtime that produces reproducible strategy results on Kraken data.

## Phase 5: Rule-Based Strategy Core

### Objective

Implement the deterministic strategy shell that defines admissible trades and hard risk constraints.

### Includes

- Universe membership enforcement for the fixed ten-asset set.
- Trend, momentum, volatility, breadth, and regime calculations.
- Rule-based candidate filtering and portfolio constraints.
- Cash allocation behavior for risk-off periods.
- Hard invalidation rules when risk conditions are unacceptable.
- Initial sell discipline and downside-handling rules.

### Deliverable

- A deterministic rule engine that can independently generate positions, exits, and risk states from historical or live inputs.

## Phase 6: Strategy Optimization and Preset Hardening

### Objective

Tune the deterministic strategy for production use and preserve reproducible preset behavior.

### Includes

- Compare named strategy presets on Kraken-based canonical data.
- Add deterministic rule-shell ablations and preset comparisons where needed for research.
- Tighten live-default risk posture while preserving the explicit max-profit variant.
- Keep preset identity reproducible across backtest, simulate, and live mode.
- Ensure research artifacts remain deterministic and auditable.

### Deliverable

- A rule-only strategy package with stable named presets and reproducible Kraken backtest evidence.

## Phase 7: Execution and Live Trading Engine

### Objective

Connect the strategy to Kraken execution safely and continuously.

### Includes

- Kraken authentication and account-state sync.
- USD cash management and balance reconciliation.
- Order creation, replace, cancel, and fill reconciliation.
- Live portfolio state management.
- Freeze-on-failure safeguards for data gaps, API failures, and execution anomalies.
- Shared order-intent path between simulate and live modes.
- Terminal monitoring output integrated into live runtime.

### Deliverable

- A continuous live trading engine for Kraken that can place and reconcile real orders in USD.

## Phase 8: CLI Product Surface

### Objective

Deliver the operator-facing interface for research, simulation, live trading, monitoring, and maintenance.

### Includes

- Implement the full CLI command tree defined in `cli.md`.
- Add setup and configuration validation commands.
- Add data ingestion and integrity-report commands.
- Add feature-build, backtest, and simulation commands.
- Add live trading and terminal monitoring commands.
- Add email recipient management commands.
- Add report export and artifact inspection commands.

### Deliverable

- A consistent CLI that can operate the full project without any GUI.

## Phase 9: Reliability, Observability, and Runbooks

### Objective

Raise the project from functional software to production-operable software.

### Includes

- Comprehensive structured logs across all major components.
- Real-time terminal monitoring view during live mode.
- Email alert integration for all specified event classes.
- Runbooks for setup, routine operation, incident handling, freeze recovery, and release validation.
- Docker-based deployment flow for local execution and future cloud transition.
- Persistence and recovery logic for restart-safe operation.

### Deliverable

- A production-operations package consisting of observability, alerts, deployment assets, and runbooks.

## Phase 10: Final Production Readiness

### Objective

Ship the final production-grade project with measurable quality gates.

### Includes

- End-to-end validation across data, strategy, simulation, and live pathways.
- CI gates for tests, build, Docker, and coverage.
- Minimum test coverage of 80%.
- Release checklist completion.
- Final documentation review to ensure docs remain authoritative.
- Final reproducibility validation from clean checkout to working system.

### Deliverable

- A production-grade Kraken spot trading bot repository that can be installed, validated, simulated, and run live through the CLI with complete supporting documentation.

## Phase 11: Interactive Operator Shell and Global Installation

### Objective

Layer a globally installed, interactive terminal shell on top of the production CLI without breaking the existing automation-friendly command surface.

### Includes

- Add a full-screen terminal shell launched by bare `tradebot` on interactive TTYs.
- Keep `tradebot <command> ...` stable for CI, scripts, Docker, and direct operator usage.
- Add `tradebot shell` as an explicit shell entrypoint and keep `tradebot init` as an explicit bootstrap/reset command.
- Move the default installed workspace to a single application home under `~/.tradebot/`, with `TRADEBOT_HOME` override support.
- Auto-create the default application home on first use when no explicit `BOT_CONFIG_PATH` override is present.
- Introduce a shared command registry and structured execution-event layer reused by both direct commands and the shell.
- Add guided parameter selection, command suggestions, readable transcript rendering, and cooperative cancellation in the shell.
- Add distribution build and publish automation for global installation through PyPI and `pipx`.

### Deliverable

- A globally installable `tradebot` package that launches an interactive terminal shell by default on interactive terminals, while preserving the full direct CLI contract for automation.

## Final Deliverable Definition

The final deliverable for this project is not just a trading algorithm.
It is a complete, documented, test-covered, Dockerized software system that includes:

- canonical historical data ingestion and validation
- a reproducible research and feature pipeline
- a full backtesting engine
- a simulation mode and a live trading mode
- a rule-only strategy with deterministic safety rails and named presets
- Kraken-only live execution in USD
- CLI-based operation
- comprehensive logs and email alerts
- CI enforcement with at least 80% test coverage
- operator runbooks and project documentation sufficient for a new developer to understand and continue the work
