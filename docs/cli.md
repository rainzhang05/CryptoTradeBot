# CLI Specification

## CLI Design Goals

- easy to use
- short commands
- clear operator intent
- suitable for both humans and automation
- no GUI dependency

## Command Naming Rule

The root command for the project is `tradebot`.

Phase 11 extends the product from direct one-shot commands into a hybrid CLI:

- `tradebot` on an interactive TTY and with no subcommand launches the interactive shell
- `tradebot shell` explicitly launches the interactive shell
- `tradebot <documented command> ...` remains supported for automation and scripts
- `tradebot --help` remains a normal one-shot help command
- `tradebot` with no args in a non-interactive context must print help and exit instead of opening a blocking shell
- when `BOT_CONFIG_PATH` is not set and the default app home does not exist yet, the first real command use must auto-create the default `~/.tradebot/` starter layout

Commands should follow a short noun-plus-action style such as:

- `tradebot run`
- `tradebot data sync`
- `tradebot backtest run`

## Global Behavior

- Commands must return non-zero exit codes on failure.
- Commands must produce human-readable terminal output.
- Commands that generate artifacts must also support machine-usable outputs where reasonable.
- `simulate` and `live` must share as much runtime behavior as possible.

## Required Command Groups

### Core runtime

- `tradebot version`: print the installed application version.
- `tradebot config-path`: print the resolved active configuration path.
- `tradebot run`: start continuous runtime.
- `tradebot stop`: stop a managed runtime if process control is implemented.
- `tradebot status`: show current runtime status, positions, balances, and health.
- `tradebot init`: bootstrap the default application home and starter configuration.
- `tradebot shell`: open the interactive operator shell explicitly.

### `tradebot stop`

This command must:

- read the tracked runtime-process metadata from `runtime/state/runtime_process.json`
- request graceful termination of the recorded process when it is still running
- return a non-zero exit when no managed runtime process is active

### `tradebot status`

This command must:

- show the tracked runtime process when one exists
- show the latest persisted runtime context and alert history when present
- show the latest live status report when live mode has run
- show the latest persisted simulate state when simulate mode has run

### Configuration and setup

- `tradebot init`: create the default application home and starter files.
- `tradebot doctor`: validate environment, config, and exchange connectivity.
- `tradebot config show`: display active non-secret configuration.
- `tradebot config validate`: validate the loaded configuration.
- `tradebot email set`: set or update the alert email recipient.
- `tradebot email test`: send a test email.

### `tradebot init`

This command must:

- create the default application home under `~/.tradebot/` unless overridden by `TRADEBOT_HOME`
- preserve `BOT_CONFIG_PATH` as the highest-precedence explicit config override for existing workflows
- create `config/settings.yaml`, `.env`, `data/`, `artifacts/`, and `runtime/` beneath the application home
- avoid overwriting existing files unless a force option is provided
- print the resolved home, config, and env paths

### `tradebot doctor`

This command must:

- validate that configuration loads successfully
- check Kraken public system status
- check private Kraken authentication when credentials are configured or required by mode
- return a non-zero exit when required connectivity checks fail

### `tradebot email set`

This command must:

- update `alerts.email_recipient` in the active YAML configuration file
- validate that the supplied value is a plausible email address

### `tradebot email test`

This command must:

- use the configured SMTP secrets from `.env`
- default to the configured `alerts.email_recipient` unless an override recipient is provided
- return a non-zero exit when SMTP configuration is incomplete or delivery fails

### Data

- `tradebot data import`: import local Kraken historical data packages.
- `tradebot data sync`: fetch missing or newer market data.
- `tradebot data check`: run integrity checks and gap reports.
- `tradebot data complete`: repair historical gaps and extend canonical data to the latest closed interval.
- `tradebot data source`: show source coverage and fallback usage.
- `tradebot data prune-raw`: remove unsupported raw Kraken files outside the fixed V1 universe.

### Research

- `tradebot features build`: build derived features.

### `tradebot features build`

This command must:

- read canonical Kraken daily candles for the selected assets
- default full-universe builds to `research.default_dataset_track`
- generate deterministic point-in-time feature rows without future leakage
- reuse cached datasets when the deterministic `dataset_id` already exists unless a force rebuild is requested
- write the dataset and manifest under `artifacts/features/<dataset_id>/`
- support `--dataset-track <track>`

### Backtesting and simulation

- `tradebot backtest run`: execute a backtest.
- `tradebot backtest report`: view or export backtest results.
- `tradebot run --mode simulate`: start continuous simulation mode.

### `tradebot backtest run`

This command must:

- build or reuse the deterministic feature dataset for the selected assets
- default full-universe backtests to `research.default_dataset_track`
- run a Kraken-only daily bar backtest using canonical `1d` candles
- generate order intents, simulated fills, and portfolio accounting from shared backtest models
- write run artifacts under `artifacts/backtests/<run_id>/`
- update `artifacts/reports/backtests/latest_backtest_report.json`
- support `--dataset-track <track>` and `--strategy-preset <preset>`
- include yearly returns, benchmarks, regime and risk distributions, action and reason counts, average exposure, and targeted-asset frequencies in the report payload

### `tradebot backtest report`

This command must:

- print the latest backtest report by default
- support loading a specific `run_id` when provided
- return a non-zero exit path if the requested report does not exist

### `tradebot run --mode simulate`

This command must:

- reuse the same target-weight and simulated execution path as the backtest service wherever practical
- load the latest persisted simulated portfolio state from `runtime/state/simulate_state.json`
- update that state after each completed simulation cycle
- return a clear waiting state when canonical data or deterministic signals are not yet available
- support `--dataset-track <track>` and `--strategy-preset <preset>`

### Live trading and monitoring

- `tradebot run --mode live`: start continuous live trading and terminal monitoring.

### `tradebot run --mode live`

This command must:

- sync Kraken balances and open orders before new decision cycles
- refresh Kraken dead-man-switch protection before placing orders
- load the latest closed canonical daily signals before each decision cycle
- build point-in-time signal rows from canonical Kraken data without forward labels
- evaluate the same deterministic strategy path used in backtests and simulate mode
- sync Kraken balances and open orders before placing new orders
- freeze on stale daily data, exchange failures, unsupported pairs, reconciliation errors, or repeated order failures
- support `--dataset-track <track>` and `--strategy-preset <preset>`
