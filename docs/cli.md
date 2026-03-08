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

- `tradebot` on an interactive TTY and with no subcommand launches the interactive shell.
- `tradebot shell` explicitly launches the interactive shell.
- `tradebot <documented command> ...` remains supported for automation and scripts.
- `tradebot --help` remains a normal one-shot help command.
- `tradebot` with no args in a non-interactive context must print help and exit instead of opening a blocking shell.
- when `BOT_CONFIG_PATH` is not set and the default app home does not exist yet, the first real command use must auto-create the default `~/.tradebot/` starter layout

Commands should follow a short noun-plus-action style such as:

- `tradebot run`
- `tradebot data sync`
- `tradebot backtest run`

This preserves the concise style the project wants while keeping commands readable.

## Global Behavior

- Commands must return non-zero exit codes on failure.
- Commands must produce human-readable terminal output.
- Commands that generate artifacts must also support machine-usable outputs where reasonable.
- `simulate` and `live` must share as much runtime behavior as possible.

## Required Command Groups

### Core runtime

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
- show the active promoted model reference when one exists

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

`tradebot init` is an explicit bootstrap/reset command, not a required first step for published installs.
Published installs must also work when the operator simply runs `tradebot` or another config-backed command first.

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

### Research and models

- `tradebot features build`: build derived features.
- `tradebot model train`: train the ML model.
- `tradebot model validate`: run validation for a model candidate.
- `tradebot model promote`: promote a model artifact after validation gates pass.

### `tradebot model train`

This command must:

- build or reuse the deterministic feature dataset for the selected assets
- train the expected-return, downside-risk, and sell-risk models on point-in-time rows only
- perform walk-forward validation across later timestamps
- require enough aligned daily timestamps after feature lookbacks and forward-label windows are applied
- write artifacts under `artifacts/models/<model_id>/`
- update `artifacts/reports/models/latest_training_summary.json`

### `tradebot model validate`

This command must:

- load the specified model artifact or default to the latest available candidate
- evaluate promotion eligibility from saved validation metrics and configured thresholds
- print human-readable validation output and support machine-usable summaries
- update `artifacts/reports/models/latest_validation_summary.json`

### `tradebot model promote`

This command must:

- refuse promotion when validation gates fail
- record the promoted model reference used by runtime and backtests
- update `artifacts/reports/models/latest_promotion_summary.json`
- make the promoted model immediately available to the shared hybrid strategy path

### `tradebot features build`

This command must:

- read canonical Kraken daily candles for the selected assets
- generate deterministic features and labels without future leakage in feature columns
- reuse cached datasets when the deterministic `dataset_id` already exists unless a force rebuild is requested
- write the dataset and manifest under `artifacts/features/<dataset_id>/`
- prepare a matching experiment root under `artifacts/experiments/<dataset_id>/`

### Backtesting and simulation

- `tradebot backtest run`: execute a backtest.
- `tradebot backtest report`: view or export backtest results.
- `tradebot run --mode simulate`: start continuous simulation mode.

### `tradebot data complete`

This command must:

- inspect the selected canonical candle files for historical gaps and stale tails
- revisit previously fallback-filled or synthetic candles and replace them with Kraken-native candles when Kraken later serves that interval
- fetch missing Kraken candles first for each unresolved interval window
- use Binance and Coinbase only as documented fallback sources when Kraken cannot close a gap
- optionally apply an explicit synthetic carry-forward candle only as a last resort so the canonical series becomes continuous to the latest closed interval
- emit progress logs with completed range count, remaining range count, and ETA based on observed per-range throughput
- write a machine-readable completion summary under `artifacts/reports/data/latest_completion_summary.json`

### `tradebot backtest run`

This command must:

- build or reuse the deterministic feature dataset for the selected assets
- run a Kraken-only daily bar backtest using canonical `1d` candles
- generate order intents, simulated fills, and portfolio accounting from shared backtest models
- enrich feature rows with promoted-model predictions when the active model matches the dataset in use
- write run artifacts under `artifacts/backtests/<run_id>/`
- update `artifacts/reports/backtests/latest_backtest_report.json`

### `tradebot backtest report`

This command must:

- print the latest backtest report by default
- support loading a specific `run_id` when provided
- return a non-zero exit path if the requested report does not exist

### `tradebot run --mode simulate`

This command must:

- reuse the same target-weight and simulated execution path as the backtest service wherever practical
- load the latest persisted simulated portfolio state from `runtime/state/simulate_state.json`
- load the active promoted model reference when available so simulation uses the same hybrid strategy path as backtests
- update that state after each completed simulation cycle
- return a clear waiting state when canonical data or deterministic signals are not yet available

### Live trading and monitoring

- `tradebot run --mode live`: start continuous live trading and terminal monitoring.

Live trading and monitoring are intentionally one runtime surface in V1.
The terminal during live mode must display monitoring information directly.

### `tradebot run --mode live`

This command must:

- require Kraken API credentials through `.env`
- refresh Kraken's dead-man switch before each live decision cycle
- sync balances and open orders from Kraken before making a new decision
- build point-in-time signal rows from canonical Kraken data without forward labels
- require an active promoted model artifact for live inference and freeze if it is missing
- place market orders only through the shared order-intent path used by simulate mode
- persist live runtime state under `runtime/state/live_state.json`
- update `artifacts/reports/runtime/latest_live_status.json` after each cycle

### Reporting and maintenance

- `tradebot report list`: list generated reports and artifacts.
- `tradebot report export`: export a chosen report.
- `tradebot logs tail`: tail recent structured logs in a readable form.

### `tradebot report list`

This command must:

- list generated files beneath `artifacts/`
- distinguish operator-facing reports from other stored artifacts

### `tradebot report export`

This command must:

- export a chosen stored report or artifact file to a target path
- return a non-zero exit when the requested source file does not exist

### `tradebot logs tail`

This command must:

- read from the durable runtime log file at `runtime/logs/tradebot.log`
- render recent JSON log lines in a human-readable form
- return a non-zero exit when no durable log file exists yet

## Command Behavior Requirements

### Interactive shell

The interactive shell must:

- render a full-screen terminal UI with header, transcript, side panels, and bottom command input
- accept shell-native commands such as `help`, `clear`, and `exit`
- accept direct command phrases such as `model train`, `data source`, and `run`
- open guided parameter selection when a command requires or benefits from option inputs
- show dropdown-style suggestions for matching commands and known-choice fields
- disable new command entry while one command is executing
- use `Ctrl-C` to cancel the active command and return the shell to idle instead of exiting the whole application
- render structured execution updates in readable transcript form rather than raw JSON logs

### Shared command layer

The shell must not shell out to Typer or spawn subprocesses to run project commands.

The implementation must define a shared command registry that:

- describes the direct command surface and shell command surface in one place
- provides field metadata, validation, and choice providers for guided shell input
- routes direct CLI handlers and shell execution through the same underlying command handlers
- preserves the existing direct command names and flags for automation compatibility

### `tradebot run`

This is the most important command.
It must:

- load validated configuration
- initialize data access
- initialize strategy and model artifacts
- initialize runtime state
- bootstrap the configured data, artifact, log, and state directories
- start either simulate or live mode
- display monitoring information continuously
- emit alerts on critical events

### Monitoring output during `tradebot run`

The terminal display must include at minimum:

- mode
- exchange connectivity state
- latest decision timestamp
- current holdings and cash
- latest model and regime summary
- recent order activity
- freeze status
- alert-worthy incidents
- terminal-rendered alert lines for newly emitted alert events

## CLI Safety Behavior

The user explicitly does not want mandatory confirmation prompts for V1 live actions.
Therefore:

- live commands should not require interactive confirmation by default
- configuration validation and runtime preflight checks must be strong enough to compensate

## Output Expectations

The CLI should produce:

- concise default output for normal use
- richer detail through flags such as verbose or JSON output if implemented
- clear failure messages with actionable cause description

## Help and Discoverability

- Every command must include help text.
- The CLI help output must reflect the command groups described in this document.
- Operator documentation must stay consistent with the implemented command tree.
