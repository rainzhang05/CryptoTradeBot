# Project Documentation

This folder is the single source of truth for the crypto spot trading bot project.
The PDF report in the repository root is reference material only and is not authoritative.
If any implementation, discussion, or legacy note conflicts with these documents, these files win.

## Document Map

### Core planning documents

- `roadmap.md`: phase-by-phase implementation plan, scope boundaries, and required deliverables
- `specification.md`: product scope, goals, constraints, and final deliverable definition

### Strategy and trading documents

- `strategy.md`: authoritative rule-only trading methodology, signal stack, sell logic, presets, and risk framework
- `data.md`: market-data policy, canonical data rules, gap handling, and storage requirements

### System and interface documents

- `architecture.md`: software architecture, runtime components, persistence model, and Docker direction
- `cli.md`: CLI command groups, naming rules, and runtime surface
- `shell-commands.md`: interactive shell command guide
- `commands.md`: concise operator-facing command reference

### Quality and operations documents

- `testing-and-quality.md`: testing strategy, CI requirements, coverage threshold, and completion rules
- `operations.md`: runtime operations, logging, alerting, safety freezes, and runbook expectations
- `runbooks.md`: operator procedures for setup, data import, simulate mode, live preflight, live operations, freeze recovery, incident investigation, and release validation
- `release-checklist.md`: final production-readiness checklist for validation, Docker, reproducibility, and live preflight gates

## Project Summary

This project is a production-grade crypto spot trading bot with these fixed V1 properties:

- Exchange execution: Kraken only
- Trading style: spot only, long-only, cash-based risk-off behavior
- Base operating currency: USD only
- User interface: CLI only
- Runtime modes: `simulate` and `live`
- Strategy shape: deterministic rule-only portfolio allocation with named presets
- Research default dataset track: `dynamic_universe_kraken_only`
- Runtime presets: `live_default` and `max_profit`
- Universe policy: fixed V1 universe of 10 Kraken-tradable large-cap assets
- Supplementary exchange data: Binance and Coinbase only for gap detection and validation, never as a blended primary signal source

## Fixed V1 Asset Universe

The V1 research and trading universe is fixed to these ten assets:

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

## Documentation Maintenance Rules

- Every implementation change that modifies scope, requirements, interfaces, or operating rules must update the relevant document in this folder.
- New code must be traceable back to at least one document in this folder.
- Ambiguous implementation decisions must be resolved here before they are treated as settled behavior.
- `roadmap.md` stays phase-oriented. The other documents stay specification-oriented.
