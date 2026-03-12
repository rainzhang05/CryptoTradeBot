# Product Specification

## Purpose

This project will produce a production-grade CLI trading bot for the crypto spot market.
The bot will trade on Kraken only in V1, use USD as the sole operating currency, and support two runtime modes: `simulate` and `live`.

The software must be usable by a single operator on a local macOS machine while remaining portable to Docker-based cloud deployment later.

## Project Goals

- Maximize long-run expected return using a deterministic rule-based strategy with aggressive but bounded risk controls.
- Trade only spot markets with no leverage, no margin, no shorts, and no derivatives.
- Keep the product CLI-first and simple to operate.
- Make simulation and live behavior as close as possible, with the primary difference being whether orders are real.
- Use Kraken as the authoritative execution venue and the authoritative evaluation venue.
- Build a full historical-data, research, backtesting, simulation, execution, and operations stack in one repository.
- Keep the project narrow and optimized for this one strategy rather than building a general-purpose multi-strategy platform.

## Non-Goals

- Multi-exchange live execution in V1.
- Frontend or web dashboard in V1.
- Support for multiple user accounts in V1.
- Derivatives or margin strategies.
- Regulatory, tax, or jurisdiction-specific compliance automation in V1.
- A generalized framework for arbitrary strategies.

## Fixed V1 Product Decisions

### Exchange and currency

- Live execution venue: Kraken only.
- Research and evaluation venue: Kraken only.
- Supplementary data venues: Binance and Coinbase for gap filling and cross-checking only.
- Base cash and accounting currency: USD only.
- Funding expectation: the operator funds the Kraken account in USD.

### Runtime modes

- `simulate`: uses the same strategy and order-intent flow as live mode but never sends real orders.
- `live`: sends real orders to Kraken and displays monitoring output in the terminal.
- Both runtime modes use the same deterministic strategy path, with named preset overrides only.

### Operator model

- Single account only.
- Continuous operation in production.
- No manual trade confirmations in V1.
- `.env` for secrets.
- One non-secret YAML configuration file for strategy and runtime settings.

### Universe

The V1 fixed research and trading universe is:

- BTC
- ETH
- BNB
- XRP
- SOL
- ADA
- DOGE
- TRX
- AVAX
- LINK

If Kraken account-region or listing constraints make any of these unavailable, the implementation must not silently substitute another asset.
Any universe change must first be reflected in this documentation set.

## Core Product Capabilities

The final system must include all of the following capabilities.

### Data capabilities

- Load user-provided Kraken historical market data packages.
- Extend Kraken history beyond local dumps using Kraken APIs.
- Store canonical cleaned data locally.
- Detect missing, duplicate, and inconsistent data.
- Use Binance and Coinbase only for gap detection and cross-checking when Kraken data is incomplete or suspect.

### Research capabilities

- Generate deterministic feature datasets from canonical data.
- Run repeatable backtests and preset comparisons.
- Produce comparable evaluation artifacts.

### Trading capabilities

- Generate portfolio decisions from the deterministic strategy.
- Manage cash and positions in USD.
- Simulate and live trade from the same strategy code path as far as possible.
- Reconcile exchange state, orders, and balances.
- Freeze automatically on critical failures.

### Operator capabilities

- Run setup, data, features, backtest, simulate, and live commands through the CLI.
- View real-time monitoring output in the terminal during live execution.
- Configure an email recipient for alerts.
- Export logs, reports, and run artifacts.

### Quality capabilities

- Maintain automated tests and CI.
- Keep test coverage at or above 80%.
- Ensure Docker builds and runtime smoke checks pass before a work session is treated as complete.

## Final Deliverable

The final deliverable is a repository that contains:

- complete authoritative documentation in `docs/`
- a working Python application for CLI-driven simulate and live trading
- canonical data ingestion and storage
- a full backtesting engine
- a rule-only strategy implementation
- a Kraken live execution engine
- comprehensive logs, reports, and email alerts
- Docker-based deployment support
- CI workflows that verify code quality, tests, coverage, and Docker health
- runbooks for operation and incident handling

## Acceptance Criteria

The project is only considered complete when all of the following are true.

- A new developer can understand the project by reading `docs/`.
- A clean checkout can be installed and validated locally.
- Historical data can be ingested and checked.
- Backtests can be run reproducibly.
- Simulation mode can run end to end.
- Live mode can connect to Kraken and trade in USD.
- Monitoring and alerting work.
- CI passes.
- Test coverage is at least 80%.
- Docker works for the supported runtime flow.

## Decision Governance

- This document and the rest of `docs/` are authoritative.
- Implementation must not outrun specification. If a major decision is missing, the docs must be updated first.
- Changes that affect strategy logic, user-facing behavior, data policy, or operational safety require documentation updates in the same work unit.
