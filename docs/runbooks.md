# Operator Runbooks

## Initial Setup

1. Run `cryptotradebot setup`.
2. Review and update `config/settings.yaml` if you need non-default settings.
3. Run `cryptotradebot kraken auth set <api-key> --secret <api-secret>` when you are ready for live trading.
4. Run `cryptotradebot run --mode simulate --max-cycles 1` as a final smoke check if desired.

## Historical Data Import

1. Place supported Kraken raw files in the configured raw data directory.
2. Run `cryptotradebot data import`.
3. Run `cryptotradebot data check`.
4. Run `cryptotradebot data complete`.
5. Run `cryptotradebot data source` if you need to inspect fallback usage.

## Feature Preparation

1. Run `cryptotradebot features build`.
2. Re-run with `--dataset-track dynamic_universe_kraken_only` when you want the long-history default explicitly.

## Backtest Validation

1. Run `cryptotradebot backtest run --strategy-preset live_default`.
2. Review the result with `cryptotradebot backtest report`.
3. Run `cryptotradebot backtest run --strategy-preset max_profit` when you want the more aggressive comparison preset.

## Simulate Mode Operation

1. Run `cryptotradebot run --mode simulate --max-cycles 1` for a short verification cycle.
2. Use `cryptotradebot status` to confirm the latest simulate snapshot and holdings state.
3. Re-run with a longer cycle count or continuous loop as needed.

## Live Mode Preflight

1. Run `cryptotradebot setup`.
2. Confirm `.env` contains valid Kraken API credentials, or run `cryptotradebot kraken auth set`.
3. Run `cryptotradebot data complete` if you want an explicit fresh repair pass.
4. Run `cryptotradebot features build`.
5. Run `cryptotradebot backtest run --strategy-preset live_default`.
6. Review `cryptotradebot backtest report`.
7. Review `cryptotradebot status` to confirm there is no unresolved freeze reason.
8. Start a short simulate cycle if needed for final sanity checking.

## Live Mode Operation

1. Start live mode with `cryptotradebot run --mode live`.
2. Monitor terminal output for:
   - current positions and USD cash
   - latest regime and risk state
   - most recent fills
   - outstanding incidents or freeze reasons
3. Use `cryptotradebot status` from another terminal if you need the persisted runtime snapshot.
4. Use `cryptotradebot stop` to request graceful termination.

## Freeze Recovery

1. Run `cryptotradebot status` and inspect:
   - latest runtime context
   - latest live status
   - latest alerts
   - freeze reason
2. If the issue is data-related:
   - run `cryptotradebot data complete`
   - rerun `cryptotradebot features build`
3. If the issue is exchange-related:
   - rerun `cryptotradebot setup`
   - verify Kraken system status and private authentication
4. If the issue is configuration-related:
   - run `cryptotradebot config validate`
   - correct `config/settings.yaml`
5. Before resuming live mode, confirm the freeze reason is gone and run a short simulate cycle if needed.

## Incident Investigation

Questions to answer:

- Was the issue exchange-related, data-related, or strategy-related?
- Did the freeze occur before or after order submission?
- Were fallback candles involved in the current signal window?
- Did the portfolio enter elevated caution, reduced aggressiveness, or catastrophe state first?

Useful commands:

- `cryptotradebot status`
- `cryptotradebot backtest report`
- `cryptotradebot data source`
- `cryptotradebot logs tail`
- `cryptotradebot report list`
- `cryptotradebot report export`

## Release Validation

1. Run `.venv/bin/python -m pytest`.
2. Run `cryptotradebot setup`.
3. Run `cryptotradebot data complete`.
4. Run `cryptotradebot features build`.
5. Run `cryptotradebot backtest run --strategy-preset live_default`.
6. Run `cryptotradebot run --mode simulate --max-cycles 1`.
7. Review the latest reports and runtime status.
