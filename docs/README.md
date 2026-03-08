# Project Documentation

This folder is the single source of truth for the crypto spot trading bot project.
The PDF report in the repository root is reference material only and is not authoritative.
If any future implementation, discussion, or legacy note conflicts with these documents, the files in this folder take precedence.

## Document Map

### Core planning documents

- `roadmap.md`: Phase-by-phase implementation plan, scope boundaries, and the required deliverable at the end of each phase.
- `specification.md`: Full product specification, project goals, non-goals, constraints, and final deliverable definition.

### Strategy and trading documents

- `strategy.md`: Authoritative trading methodology, signal stack, hybrid rule-based plus ML design, ranking logic, sell logic, and risk framework.
- `data.md`: Market data sources, historical dataset policy, gap-handling rules, cross-exchange validation policy, and storage requirements.

### System and interface documents

- `architecture.md`: Software architecture, runtime components, persistence model, Docker target, and environment assumptions.
- `cli.md`: CLI command groups, command naming rules, operator workflows, and mode behavior.

### Quality and operations documents

- `testing-and-quality.md`: Testing strategy, CI requirements, coverage threshold, validation gates, and release quality bar.
- `operations.md`: Runtime operations, logging, alerting, email notifications, safety freezes, and runbook expectations.
- `runbooks.md`: Operator procedures for setup, historical data import, simulate mode, live preflight, live operations, freeze recovery, incident investigation, and release validation.

## Project Summary

This project will build a production-grade crypto spot trading bot with these fixed high-level properties:

- Exchange execution for V1: Kraken only.
- Trading style: spot only, long-only, cash-based risk-off behavior.
- Base operating currency: USD only for data, research, accounting, and execution.
- User interface: CLI only for V1.
- Runtime modes: `simulate` and `live`.
- Strategy shape: hybrid system with a rule-based decision shell and an ML prediction layer that improves ranking and downside-handling decisions.
- Universe policy: fixed V1 universe of 10 Kraken-tradable large-cap assets.
- Supplementary exchange data: Binance and Coinbase only for gap detection and cross-checking, not as a blended primary signal source.

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
- Ambiguous implementation decisions must be resolved here before being treated as settled project behavior.
- The roadmap should remain phase-oriented. The other documents should remain specification-oriented.
