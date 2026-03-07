# Crypto Trading Bot for Spot Market

Systematic crypto spot trading bot for Kraken spot markets with a CLI-first operator workflow, hybrid rule-based plus ML strategy design, and full support for research, simulation, backtesting, and live execution.

## Status

The repository is in Phase 1 of the roadmap.
The current implementation provides the Python project skeleton, configuration foundation, logging foundation, test tooling, Docker support, and CI scaffolding that later phases will build on.

## Quick Start

1. Install `uv`.
2. Sync dependencies: `uv sync --extra dev`
3. Copy `.env.example` to `.env` and adjust values as needed.
4. Review `config/settings.yaml`.
5. Run the CLI: `uv run bot --help`

## Source of Truth

Project requirements and implementation phases are defined in the docs folder.
Start with [docs/README.md](docs/README.md).
