# Operator Runbooks

## Initial Setup

1. Run `tradebot init` if you want an explicit bootstrap step.
2. Review and update `config/settings.yaml`.
3. Populate `.env` with Kraken credentials for live trading and SMTP settings for alert delivery if desired.
4. Run `tradebot doctor`.

## Historical Data Import

1. Place supported Kraken raw files in the configured raw data directory.
2. Run `tradebot data import`.
3. Run `tradebot data check`.
4. Run `tradebot data complete`.
5. Run `tradebot data source` if you need to inspect fallback usage.

## Feature Preparation

1. Run `tradebot features build`.
2. Re-run with `--dataset-track dynamic_universe_kraken_only` when you want the long-history default explicitly.

## Backtest Validation

1. Run `tradebot backtest run --strategy-preset live_default`.
2. Review the result with `tradebot backtest report`.
3. Run `tradebot backtest run --strategy-preset max_profit` when you want the more aggressive comparison preset.

## Simulate Mode Operation

1. Run `tradebot run --mode simulate --max-cycles 1` for a short verification cycle.
2. Use `tradebot status` to confirm the latest simulate snapshot and holdings state.
3. Re-run with a longer cycle count or continuous loop as needed.

## Live Mode Preflight

1. Confirm `.env` contains valid Kraken API credentials.
2. Run `tradebot doctor`.
3. Run `tradebot data complete`.
4. Run `tradebot features build`.
5. Run `tradebot backtest run --strategy-preset live_default`.
6. Review `tradebot backtest report`.
7. Review `tradebot status` to confirm there is no unresolved freeze reason.
8. Start a short simulate cycle if needed for final sanity checking.

## Live Mode Operation

1. Start live mode with `tradebot run --mode live`.
2. Monitor terminal output for:
   - current positions and USD cash
   - latest regime and risk state
   - most recent fills
   - outstanding incidents or freeze reasons
3. Use `tradebot status` from another terminal if you need the persisted runtime snapshot.
4. Use `tradebot stop` to request graceful termination.

## Freeze Recovery

1. Run `tradebot status` and inspect:
   - latest runtime context
   - latest live status
   - latest alerts
   - freeze reason
2. If the issue is data-related:
   - run `tradebot data complete`
   - rerun `tradebot features build`
3. If the issue is exchange-related:
   - rerun `tradebot doctor`
   - verify Kraken system status and private authentication
4. If the issue is configuration-related:
   - run `tradebot config validate`
   - correct `config/settings.yaml`
5. Before resuming live mode, confirm the freeze reason is gone and run a short simulate cycle if needed.

## Incident Investigation

Questions to answer:

- Was the issue exchange-related, data-related, or strategy-related?
- Did the freeze occur before or after order submission?
- Were fallback candles involved in the current signal window?
- Did the portfolio enter elevated caution, reduced aggressiveness, or catastrophe state first?

Useful commands:

- `tradebot status`
- `tradebot backtest report`
- `tradebot data source`
- `tradebot logs tail`
- `tradebot report list`
- `tradebot report export`

## Release Validation

1. Run `.venv/bin/python -m pytest`.
2. Run `tradebot doctor`.
3. Run `tradebot data complete`.
4. Run `tradebot features build`.
5. Run `tradebot backtest run --strategy-preset live_default`.
6. Run `tradebot run --mode simulate --max-cycles 1`.
7. Review the latest reports and runtime status.
