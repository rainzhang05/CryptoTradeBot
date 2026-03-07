# Crypto Trading Bot for Spot Market

Systematic crypto spot trading bot for Kraken spot markets with a CLI-first operator workflow, hybrid rule-based plus ML strategy design, and full support for research, simulation, backtesting, and live execution.

## Status

The repository is in Phase 3 of the roadmap.
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
- `uv run bot data prune-raw`

Useful Phase 3 research command:

- `uv run bot features build`

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
