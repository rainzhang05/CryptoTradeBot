# Command Reference

This file is the concise operator-facing command index for `tradebot`.
For the full authoritative behavior, flags, and requirements, see [`cli.md`](./cli.md).

## Shell

- `tradebot`: open the interactive operator shell on an interactive terminal.
- `tradebot shell`: open the interactive operator shell explicitly.
- `tradebot init`: create or refresh the default application home under `~/.tradebot/`.
- `tradebot version`: print the installed application version.
- `tradebot config-path`: print the resolved active configuration path.

## Configuration

- `tradebot doctor`: validate configuration, environment, and Kraken connectivity.
- `tradebot config show`: print the active non-secret configuration.
- `tradebot config validate`: validate the active configuration file.
- `tradebot email set <recipient>`: set the alert email recipient.
- `tradebot email test [--recipient ...]`: send a test email with the configured SMTP settings.

## Data

- `tradebot data import`: import local Kraken historical files into canonical datasets.
- `tradebot data sync`: fetch newer or missing market data.
- `tradebot data check`: run integrity checks and generate a gap report.
- `tradebot data complete`: repair historical gaps and extend data to the latest closed interval.
- `tradebot data source`: show source coverage and fallback usage.
- `tradebot data prune-raw`: delete raw Kraken CSV files that are outside the fixed V1 universe.

## Research And Models

- `tradebot features build [--dataset-track TRACK]`: build or reuse the deterministic feature dataset.
- `tradebot research sweep [--preset broad_staged] [--resume] [--max-workers N] [--limit N]`: run the staged research evaluation harness.
- `tradebot research report [sweep_id]`: show the latest or a specific research sweep report.
- `tradebot model train [--dataset-track TRACK] [--family FAMILY]`: train the expected-return, downside-risk, and sell-risk models.
- `tradebot model validate`: evaluate whether a model artifact is eligible for promotion.
- `tradebot model promote`: promote a validated model only after it beats the rule-only Kraken backtest baseline for the same dataset.

## Backtesting And Runtime

- `tradebot backtest run [--dataset-track TRACK] [--model-id ID] [--use-active-model/--no-use-active-model]`: run a Kraken-only backtest from canonical daily data.
- `tradebot backtest report [run_id]`: show the latest or a specific backtest report.
- `tradebot run --mode simulate [--dataset-track TRACK]`: start the shared runtime in simulate mode.
- `tradebot run --mode live [--dataset-track TRACK]`: start the shared runtime in live mode.
- `tradebot status`: show the latest known runtime, portfolio, and health state.
- `tradebot stop`: request termination of a tracked managed runtime process.

## Reports And Logs

- `tradebot report list`: list stored reports and artifacts.
- `tradebot report export <source> <destination>`: copy one stored report or artifact to a target path.
- `tradebot logs tail [--lines N]`: render recent durable JSON logs in a readable format.
