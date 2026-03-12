# Testing and Quality Specification

## Quality Bar

The project must maintain engineering quality high enough for a production trading system.

## Mandatory Thresholds

- automated test coverage must remain at or above 80%
- all CI workflows must pass before a work session is considered complete
- Docker build and runtime smoke checks must pass in CI once the application exists

## Required Test Types

### Unit tests

Required for:

- feature calculations
- strategy rules
- portfolio math
- configuration loading and validation
- exchange adapter logic that can be isolated

### Integration tests

Required for:

- data ingestion flows
- canonicalization pipeline
- backtest orchestration
- CLI command behavior
- runtime startup and freeze handling

### Regression tests

Required for:

- deterministic strategy outputs on fixture datasets
- dynamic-universe feature generation and active-universe breadth handling
- simulate versus expected-fill behavior
- backtest report generation consistency
- preset comparison behavior where presets intentionally diverge

### End-to-end or smoke tests

Required for:

- CLI startup path
- Docker image execution
- representative simulate-mode flow

## Strategy-Specific Validation Requirements

- compare named rule-only presets against each other where behavior intentionally differs
- ensure deterministic datasets contain only point-in-time feature values
- ensure Kraken-based evaluation remains the official benchmark

## CI Requirements

The repository must include GitHub Actions workflows that eventually verify all of the following.

- dependency installation
- code formatting or linting
- static checks or type checks where adopted
- unit and integration tests
- coverage threshold enforcement
- Docker build success
- runtime smoke checks that are safe for CI

## Coverage Enforcement

- Coverage must be enforced in CI, not just reported.
- New code without tests is incomplete work.
- If a change makes 80% coverage impossible for a justified reason, the documentation must explain why and the exception must be explicitly approved in the docs before merging. The default assumption is that no exception exists.

## Work Session Completion Rule

A coding work session is not complete unless:

- relevant tests were added or updated
- local validation was run where practical
- CI configuration was created or updated when affected
- the repository is left in a passing state or the blocker is explicitly documented

## Commit Discipline

Commits should be incremental and scoped.
Large unrelated batches are not acceptable.

Each logical change should include:

- implementation
- tests
- documentation updates when behavior changes

## Documentation Quality Rule

If implementation changes expected behavior, operator workflow, or strategy logic, the relevant docs must be updated in the same logical change set.

## Release Readiness Checklist

Before the final production release, the project must be able to demonstrate:

- reproducible install from clean checkout
- reproducible data preparation path
- reproducible backtest path
- successful simulate run
- successful live preflight path
- passing CI
- passing Docker flow
- 80% or higher coverage

The explicit final release gate is maintained in `docs/release-checklist.md`.
