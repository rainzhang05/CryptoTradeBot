# Crypto Trading Bot for Spot Market

Systematic crypto spot trading bot for Kraken spot markets with a CLI-first operator workflow, hybrid rule-based plus ML strategy design, and current Phase 7 support for data preparation, research, backtesting, simulation, live execution, and ML-assisted portfolio decisions.

## Status

The repository is in Phase 7 of the roadmap.
The current implementation provides:

- the Python project skeleton and toolchain foundation
- typed configuration loading from YAML and `.env`
- structured logging
- CLI scaffolding and runtime bootstrap commands
- Docker and GitHub Actions CI validation
- raw Kraken trade-data ingestion for the fixed V1 universe
- canonical 1-hour and 1-day candle generation
- canonical data integrity reports
- source coverage reporting
- incremental Kraken sync with Binance and Coinbase fallback support when needed
- deterministic daily feature generation from canonical Kraken data
- BTC-led regime features, breadth metrics, liquidity features, and source-confidence features
- label generation for forward return, downside risk, and sell-risk modeling
- cached experiment-ready datasets with manifests and experiment directory conventions
- a deterministic daily backtest engine with conservative fee and slippage assumptions
- backtest artifacts including report, fills, equity curve, and decision logs
- a simulate-mode runtime path that reuses the backtest decision and execution flow
- persisted simulate portfolio state for restart-safe local iteration
- a deterministic rule engine with universe enforcement, regime-aware cash allocation, hard data-quality vetoes, freeze handling, and gradual reduction/full-exit rules
- drawdown-aware risk states that reduce aggressiveness before catastrophe conditions
- a walk-forward ML training and validation pipeline built on deterministic Phase 3 feature datasets
- versioned model artifacts with manifests, metrics, predictions, and promotion metadata
- hybrid rule-plus-ML portfolio decisions that consume promoted model predictions when available
- CLI commands for model training, validation, and promotion
- a Kraken-authenticated live execution service with account sync, order submission, dead-man switch refresh, fill reconciliation, and persisted live state
- a shared runtime loop for simulate and live modes with continuous terminal monitoring output
- freeze-on-failure safeguards for stale data, missing active models, order-management errors, and reconciliation anomalies

Later phases still cover the broader CLI surface, email alert delivery, runbooks, and final production-hardening work from the roadmap.

## Quick Start

1. Install `uv`.
2. Sync dependencies: `uv sync --python 3.12 --extra dev`
3. Copy `.env.example` to `.env` and adjust values as needed.
4. Review `config/settings.yaml`.
5. Run the CLI: `uv run bot --help`

Useful Phase 2 data commands:

- `uv run bot data source`
- `uv run bot data import`
- `uv run bot data check`
- `uv run bot data sync`
- `uv run bot data complete`
- `uv run bot data prune-raw`

Useful Phase 3 research command:

- `uv run bot features build`

Useful Phase 4 backtest and simulation commands:

- `uv run bot backtest run`
- `uv run bot backtest report`
- `uv run bot run --mode simulate --max-cycles 1`

Useful Phase 6 model commands:

- `uv run bot model train`
- `uv run bot model validate`
- `uv run bot model promote`

Useful Phase 7 live-runtime command:

- `uv run bot run --mode live --max-cycles 1`

## Docker

Build the container:

- `docker build -t crypto-spot-trading-bot .`

Run a preflight check in the container:

- `docker run --rm crypto-spot-trading-bot doctor`

Use the local compose workflow:

- `docker compose run --rm bot`

## Source of Truth

Project requirements and implementation phases are defined in the docs folder.
Start with [docs/README.md](docs/README.md).
