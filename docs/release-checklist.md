# Release Checklist

This checklist defines the final phase 10 release gate for the repository.
All items must be satisfied before treating the project as production-ready.
Command examples assume the local environment is active from `source .venv/bin/activate`.

## Repository and Documentation

- `docs/` reflects the implemented behavior and remains the source of truth.
- `README.md` accurately describes the current project status and operator entry points.
- `docs/runbooks.md` is current for setup, simulate, live preflight, live operations, freeze recovery, incident response, and release validation.
- No undocumented changes exist for strategy logic, CLI behavior, data policy, or operational safety.

## Validation and Quality Gates

- `ruff check src tests`
- `mypy src`
- `pytest`
- Coverage remains at or above 80%.
- The end-to-end release-readiness integration flow passes.
- Live-runtime safety tests pass.

## Docker and Deployment

- `docker compose config` succeeds.
- `docker build -t crypto-spot-trading-bot .` succeeds in an environment with a running Docker daemon.
- Container smoke checks pass for CLI startup, data import, and feature generation.
- The compose-based local deployment path remains valid.

## Reproducibility

- A clean checkout can install with `uv sync --frozen --extra dev`.
- The repository can create canonical data from the provided fixtures.
- The repository can build features, train and promote a model, run a backtest, and run simulate mode from a fresh workspace.
- Operator status, report export, email-recipient configuration, and log-tail workflows all function from that clean workspace.

## Live Preflight Readiness

- `tradebot doctor` remains the required live preflight command.
- Live mode still requires Kraken credentials and freezes when the promoted model or data prerequisites are missing.
- Email alert routing remains configurable and testable through the CLI.
