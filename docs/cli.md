# CLI Specification

## CLI Design Goals

- easy to use
- short commands
- clear operator intent
- suitable for both humans and automation
- no GUI dependency

## Command Naming Rule

The root command for the project is `cryptotradebot`.

Phase 11 extends the product from direct one-shot commands into a hybrid CLI:

- `cryptotradebot` on an interactive TTY and with no subcommand launches the interactive shell
- `cryptotradebot shell` explicitly launches the interactive shell
- `cryptotradebot <documented command> ...` remains supported for automation and scripts
- `cryptotradebot --help` remains a normal one-shot help command
- `cryptotradebot` with no args in a non-interactive context must print help and exit instead of opening a blocking shell
- when `CRYPTOTRADEBOT_CONFIG_PATH` is not set and the default app home does not exist yet, the first real command use must auto-create the default `~/.cryptotradebot/` starter layout

Commands should follow a short noun-plus-action style such as:

- `cryptotradebot run`
- `cryptotradebot setup`
- `cryptotradebot backtest run`

## Global Behavior

- Commands must return non-zero exit codes on failure.
- Commands must produce human-readable terminal output.
- Commands that generate artifacts must also support machine-usable outputs where reasonable.
- `simulate` and `live` must share as much runtime behavior as possible.

## Required Command Groups

### Core runtime

- `cryptotradebot version`: print the installed application version.
- `cryptotradebot config-path`: print the resolved active configuration path.
- `cryptotradebot run`: start continuous runtime.
- `cryptotradebot stop`: stop a managed runtime if process control is implemented.
- `cryptotradebot status`: show current runtime status, positions, balances, and health.
- `cryptotradebot setup`: initialize the application home, prepare runtime-ready data, and run readiness checks.
- `cryptotradebot shell`: open the interactive operator shell explicitly.

### `cryptotradebot stop`

This command must:

- read the tracked runtime-process metadata from `runtime/state/runtime_process.json`
- request graceful termination of the recorded process when it is still running
- return a non-zero exit when no managed runtime process is active

### `cryptotradebot status`

This command must:

- show the tracked runtime process when one exists
- show the latest persisted runtime context and alert history when present
- show the latest live status report when live mode has run
- show the latest persisted simulate state when simulate mode has run

### Configuration and setup

- `cryptotradebot setup`: create the default application home if needed, prepare runtime-ready canonical data, build the deterministic feature cache, and validate non-secret runtime prerequisites.
- `cryptotradebot kraken auth set`: write Kraken API credentials into the active `.env`.
- `cryptotradebot config show`: display active non-secret configuration.
- `cryptotradebot config validate`: validate the loaded configuration.
- `cryptotradebot email set`: set or update the alert email recipient.
- `cryptotradebot email test`: send a test email.

### `cryptotradebot setup`

This command must:

- create the default application home under `~/.cryptotradebot/` unless overridden by `CRYPTOTRADEBOT_HOME`
- preserve `CRYPTOTRADEBOT_CONFIG_PATH` as the highest-precedence explicit config override for existing workflows while honoring the older override names for compatibility
- create `config/settings.yaml`, `.env`, `data/`, `artifacts/`, and `runtime/` beneath the application home when they do not already exist
- bootstrap a recent Kraken-native canonical data window sufficient for live and simulate mode when a fuller local history does not yet exist
- run canonical completion, integrity validation, and deterministic feature preparation needed for live and simulate readiness
- validate configuration loading and Kraken public system status
- report Kraken private-auth status without treating missing credentials as a fatal setup failure
- print the resolved home, config, env, data, and readiness status

### `cryptotradebot kraken auth set`

This command must:

- write the supplied Kraken API key into the active `.env`
- optionally write the supplied Kraken API secret and OTP into the active `.env`
- avoid printing secrets back to the terminal
- report whether private credentials are now complete enough for live mode

### `cryptotradebot email set`

This command must:

- update `alerts.email_recipient` in the active YAML configuration file
- validate that the supplied value is a plausible email address

### `cryptotradebot email test`

This command must:

- use the configured SMTP secrets from `.env`
- default to the configured `alerts.email_recipient` unless an override recipient is provided
- return a non-zero exit when SMTP configuration is incomplete or delivery fails

### Data

- `cryptotradebot data import`: import local Kraken historical data packages.
- `cryptotradebot data sync`: fetch missing or newer market data.
- `cryptotradebot data check`: run integrity checks and gap reports.
- `cryptotradebot data complete`: repair historical gaps and extend canonical data to the latest closed interval.
- `cryptotradebot data source`: show source coverage and fallback usage.
- `cryptotradebot data prune-raw`: remove unsupported raw Kraken files outside the fixed V1 universe.

### Research

- `cryptotradebot features build`: build derived features.

### `cryptotradebot features build`

This command must:

- read canonical Kraken daily candles for the selected assets
- default full-universe builds to `research.default_dataset_track`
- generate deterministic point-in-time feature rows without future leakage
- reuse cached datasets when the deterministic `dataset_id` already exists unless a force rebuild is requested
- write the dataset and manifest under `artifacts/features/<dataset_id>/`
- support `--dataset-track <track>`

### Backtesting and simulation

- `cryptotradebot backtest run`: execute a backtest.
- `cryptotradebot backtest report`: view or export backtest results.
- `cryptotradebot run --mode simulate`: start continuous simulation mode.

### `cryptotradebot backtest run`

This command must:

- build or reuse the deterministic feature dataset for the selected assets
- default full-universe backtests to `research.default_dataset_track`
- run a Kraken-only daily bar backtest using canonical `1d` candles
- generate order intents, simulated fills, and portfolio accounting from shared backtest models
- write run artifacts under `artifacts/backtests/<run_id>/`
- update `artifacts/reports/backtests/latest_backtest_report.json`
- support `--dataset-track <track>` and `--strategy-preset <preset>`
- include yearly returns, benchmarks, regime and risk distributions, action and reason counts, average exposure, and targeted-asset frequencies in the report payload

### `cryptotradebot backtest report`

This command must:

- print the latest backtest report by default
- support loading a specific `run_id` when provided
- return a non-zero exit path if the requested report does not exist

### `cryptotradebot run --mode simulate`

This command must:

- reuse the same target-weight and simulated execution path as the backtest service wherever practical
- load the latest persisted simulated portfolio state from `runtime/state/simulate_state.json`
- update that state after each completed simulation cycle
- return a clear waiting state when canonical data or deterministic signals are not yet available
- support `--dataset-track <track>` and `--strategy-preset <preset>`

### Live trading and monitoring

- `cryptotradebot run --mode live`: start continuous live trading and terminal monitoring.

### `cryptotradebot run --mode live`

This command must:

- sync Kraken balances and open orders before new decision cycles
- refresh Kraken dead-man-switch protection before placing orders
- load the latest closed canonical daily signals before each decision cycle
- build point-in-time signal rows from canonical Kraken data without forward labels
- evaluate the same deterministic strategy path used in backtests and simulate mode
- sync Kraken balances and open orders before placing new orders
- freeze on stale daily data, exchange failures, unsupported pairs, reconciliation errors, or repeated order failures
- support `--dataset-track <track>` and `--strategy-preset <preset>`
