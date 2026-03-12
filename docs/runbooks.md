# Operator Runbooks

This file defines the operator runbooks required by `docs/operations.md`.
All commands assume the repository root as the working directory and an active local
environment from `source .venv/bin/activate` unless noted otherwise.

## 0. Global Install

For a published release install:

1. Run `pipx install CryptoTradeBot`.
2. Launch the shell with `tradebot`.

Expected outcome:

- `tradebot` works from any directory
- the default application home is `~/.tradebot/` unless `TRADEBOT_HOME` is set
- starter files exist under `~/.tradebot/config/settings.yaml` and `~/.tradebot/.env`
- the starter files are created automatically on first use when no explicit `BOT_CONFIG_PATH` is set

## 1. Initial Setup

1. Install `uv` and Docker.
2. Sync dependencies with `uv sync --python 3.12 --extra dev`.
3. Activate the local environment with `source .venv/bin/activate`.
4. Copy `.env.example` to `.env` and fill in Kraken and SMTP secrets as needed.
5. Review `config/settings.yaml`.
6. Run `tradebot doctor`.

Expected outcome:

- configuration loads successfully
- Kraken public connectivity is healthy
- private Kraken authentication passes when live credentials are configured
- the repository paths in the doctor output point at the expected local directories

## 2. Historical Data Import

1. Place raw Kraken CSV files in `data/kraken_data/`.
2. Run `tradebot data import`.
3. Run `tradebot data check`.
4. Run `tradebot data source`.
5. If gaps remain, run `tradebot data complete`.

Expected artifacts:

- canonical candles under `data/canonical/kraken/<ASSET>/`
- integrity reports under `artifacts/reports/data/`
- completion summaries under `artifacts/reports/data/latest_completion_summary.json`

## 3. Simulate Mode Operation

1. Build or refresh features with `tradebot features build`.
2. Train, validate, and promote a model when the feature dataset has changed:
   - `tradebot model train`
   - `tradebot model validate`
   - `tradebot model promote`
     This promotion step rechecks Kraken backtest uplift against the rule-only baseline and refuses promotion if the hybrid candidate does not improve on it.
3. Start simulation with `tradebot run --mode simulate`.
   Use `--strategy-preset max_profit` only for explicit research comparison runs. The checked-in
   default is the hardened `live_default` preset.
4. Inspect status with `tradebot status`.
5. Tail logs with `tradebot logs tail --lines 50`.

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
3. Confirm the alert recipient with `tradebot email set trader@example.com` if needed.
4. Verify SMTP delivery with `tradebot email test`.
5. Verify exchange connectivity with `tradebot doctor`.
6. Confirm canonical daily data is current with `tradebot data complete`.
7. Review `tradebot status` to see whether live mode will use a compatible promoted model or
   operate in rule-only fallback mode.

Do not start live mode when:

- doctor reports failed exchange checks
- latest daily signals are stale
- the alert recipient or SMTP settings are intentionally absent for a monitored live run

## 5. Live Mode Operation

1. Start the live runtime with `tradebot run --mode live`.
   Use `--strategy-preset max_profit` only for explicit research or dry-run comparison, not as the
   unattended live default.
2. Watch the terminal monitoring output for:
   - mode, connectivity, regime, risk, holdings, and cash
   - model summary, or rule-only fallback incidents when no compatible promoted model is active
   - recent fills
   - alert lines beginning with `ALERT`
3. Use `tradebot status` from another terminal to inspect:
   - the managed runtime process
   - latest runtime context
   - latest alert history
   - live state and live status summary
4. Use `tradebot stop` for a managed graceful stop.

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

1. Run `tradebot status`.
2. Identify `freeze_reason` in:
   - terminal alert output
   - `artifacts/reports/runtime/latest_live_status.json`
   - `runtime/state/live_state.json`
   - `runtime/state/runtime_context.json`
3. Inspect the recent durable logs with `tradebot logs tail --lines 100`.
4. Classify the freeze:
   - exchange or API failure
   - data gap or integrity failure
   - model or inference failure
   - reconciliation or order-management failure
5. Fix the underlying issue.
6. Re-run `tradebot doctor`.
7. Re-run `tradebot data complete` if data freshness or source confidence was involved.
8. Confirm the promoted model is still valid with `tradebot status` when live mode is expected to
   run with ML predictions.
9. Restart `tradebot run --mode live` only after the preflight checks pass again.

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

1. Run `ruff check src tests`.
2. Run `mypy src`.
3. Run `pytest`.
4. Build Docker with `docker build -t cryptotradebot .`.
5. Run the safe container preflight with `docker run --rm cryptotradebot doctor`.
6. Run the compose preflight with `docker compose run --rm tradebot`.
7. Validate the published install smoke path with `tradebot --help`, `tradebot config validate`, and `tradebot` in an isolated environment when release packaging changed.

The release candidate is valid only when:

- tests pass with coverage at or above 80%
- Docker builds successfully
- the documented CLI flows still work
- the operational reports and runbooks remain consistent with the implementation
- the checklist in `docs/release-checklist.md` is fully satisfied
