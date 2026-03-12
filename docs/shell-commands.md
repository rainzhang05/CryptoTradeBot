# Shell Command Guide

The interactive shell supports the same documented command surface as the direct CLI, plus a few shell-only shortcuts.

## Shell-only shortcuts

- `help`: show the shell command list
- `clear`: clear the transcript view
- `exit`: close the shell

## Supported operator commands

- `setup`: initialize the application home, prepare runtime-ready data, and run readiness checks.
- `run`: start the configured runtime mode. The shell form exposes mode, cycle limit, dataset track, and strategy preset.
- `stop`: request termination of the tracked runtime process.
- `status`: show the latest known runtime status.
- `kraken auth set`: store Kraken credentials in the active `.env`.
- `config show`: print the active non-secret configuration.
- `config validate`: validate the active configuration.
- `data import`: import raw Kraken data.
- `data sync`: extend canonical market data.
- `data check`: validate canonical candles.
- `data complete`: repair gaps and extend canonical candles.
- `data source`: inspect source coverage and fallback usage.
- `data prune-raw`: remove unsupported raw files.
- `features build`: build deterministic point-in-time feature datasets.
- `backtest run`: execute a backtest using the current configuration. The shell form exposes the dataset track and optional strategy preset.
- `backtest report`: inspect the latest or a specific backtest report.
- `report list`: list stored reports and artifacts.
- `report export`: export one stored report or artifact.
- `email set`: update the alert recipient.
- `email test`: send a test email.
- `logs tail`: render recent durable logs.
