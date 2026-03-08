# Operator Runbooks

This file defines the operator runbooks required by `docs/operations.md`.
All commands assume the repository root as the working directory.

## 1. Initial Setup

1. Install `uv` and Docker.
2. Sync dependencies with `uv sync --python 3.12 --extra dev`.
3. Copy `.env.example` to `.env` and fill in Kraken and SMTP secrets as needed.
4. Review `config/settings.yaml`.
5. Run `uv run bot doctor`.

Expected outcome:

- configuration loads successfully
- Kraken public connectivity is healthy
- private Kraken authentication passes when live credentials are configured
- the repository paths in the doctor output point at the expected local directories

## 2. Historical Data Import

1. Place raw Kraken CSV files in `data/kraken_data/`.
2. Run `uv run bot data import`.
3. Run `uv run bot data check`.
4. Run `uv run bot data source`.
5. If gaps remain, run `uv run bot data complete`.

Expected artifacts:

- canonical candles under `data/canonical/kraken/<ASSET>/`
- integrity reports under `artifacts/reports/data/`
- completion summaries under `artifacts/reports/data/latest_completion_summary.json`

## 3. Simulate Mode Operation

1. Build or refresh features with `uv run bot features build`.
2. Train, validate, and promote a model when the feature dataset has changed:
   - `uv run bot model train`
   - `uv run bot model validate`
   - `uv run bot model promote`
3. Start simulation with `uv run bot run --mode simulate`.
4. Inspect status with `uv run bot status`.
5. Tail logs with `uv run bot logs tail --lines 50`.

Expected runtime files:

- simulated portfolio state: `runtime/state/simulate_state.json`
- runtime context: `runtime/state/runtime_context.json`
- alert deduplication state: `runtime/state/alert_state.json`
- latest runtime context report: `artifacts/reports/runtime/latest_runtime_context.json`
- latest alert history: `artifacts/reports/runtime/latest_alerts.json`

## 4. Live Mode Preflight

Run this checklist before every live session:

1. Confirm `KRAKEN_API_KEY` and `KRAKEN_API_SECRET` are present in `.env`.
2. Confirm SMTP settings are present when email alerts are expected.
3. Confirm the alert recipient with `uv run bot email set trader@example.com` if needed.
4. Verify SMTP delivery with `uv run bot email test`.
5. Verify exchange connectivity with `uv run bot doctor`.
6. Confirm an active promoted model exists with `uv run bot status`.
7. Confirm canonical daily data is current with `uv run bot data complete`.

Do not start live mode when:

- doctor reports failed exchange checks
- there is no promoted model
- latest daily signals are stale
- the alert recipient or SMTP settings are intentionally absent for a monitored live run

## 5. Live Mode Operation

1. Start the live runtime with `uv run bot run --mode live`.
2. Watch the terminal monitoring output for:
   - mode, connectivity, regime, risk, holdings, and cash
   - model summary and recent fills
   - alert lines beginning with `ALERT`
3. Use `uv run bot status` from another terminal to inspect:
   - the managed runtime process
   - latest runtime context
   - latest alert history
   - live state and live status summary
4. Use `uv run bot stop` for a managed graceful stop.

Primary live artifacts:

- `runtime/state/live_state.json`
- `runtime/state/runtime_process.json`
- `runtime/state/runtime_context.json`
- `runtime/state/alert_state.json`
- `artifacts/reports/runtime/latest_live_status.json`
- `artifacts/reports/runtime/latest_runtime_context.json`
- `artifacts/reports/runtime/latest_alerts.json`
- `runtime/logs/tradebot.log`

## 6. Freeze Recovery

When the bot freezes:

1. Run `uv run bot status`.
2. Identify `freeze_reason` in:
   - terminal alert output
   - `artifacts/reports/runtime/latest_live_status.json`
   - `runtime/state/live_state.json`
   - `runtime/state/runtime_context.json`
3. Inspect the recent durable logs with `uv run bot logs tail --lines 100`.
4. Classify the freeze:
   - exchange or API failure
   - data gap or integrity failure
   - model or inference failure
   - reconciliation or order-management failure
5. Fix the underlying issue.
6. Re-run `uv run bot doctor`.
7. Re-run `uv run bot data complete` if data freshness or source confidence was involved.
8. Confirm the promoted model is still valid with `uv run bot status`.
9. Restart `uv run bot run --mode live` only after the preflight checks pass again.

## 7. Incident Investigation

Use this order of evidence:

1. `runtime/state/runtime_context.json`
2. `artifacts/reports/runtime/latest_alerts.json`
3. `artifacts/reports/runtime/latest_live_status.json`
4. `runtime/state/live_state.json` or `runtime/state/simulate_state.json`
5. `runtime/logs/tradebot.log`

Questions to answer:

- What was the most recent decision timestamp?
- What regime and risk state were active?
- Which alert class fired first?
- Did the bot execute trades, reduce risk, or freeze?
- Was the issue exchange-related, data-related, or model-related?

## 8. Release Validation

Before treating a work session as complete:

1. Run `uv run ruff check src tests`.
2. Run `uv run mypy src`.
3. Run `uv run pytest`.
4. Build Docker with `docker build -t crypto-spot-trading-bot .`.
5. Run the safe container preflight with `docker run --rm crypto-spot-trading-bot doctor`.
6. Run the compose preflight with `docker compose run --rm bot`.

The release candidate is valid only when:

- tests pass with coverage at or above 80%
- Docker builds successfully
- the documented CLI flows still work
- the operational reports and runbooks remain consistent with the implementation
- the checklist in `docs/release-checklist.md` is fully satisfied
