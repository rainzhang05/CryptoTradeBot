# CLI Specification

## CLI Design Goals

- easy to use
- short commands
- clear operator intent
- suitable for both humans and automation
- no GUI dependency

## Command Naming Rule

The root command for the project is `bot`.

Commands should follow a short noun-plus-action style such as:

- `bot run`
- `bot data sync`
- `bot backtest run`

This preserves the concise style the project wants while keeping commands readable.

## Global Behavior

- Commands must return non-zero exit codes on failure.
- Commands must produce human-readable terminal output.
- Commands that generate artifacts must also support machine-usable outputs where reasonable.
- `simulate` and `live` must share as much runtime behavior as possible.

## Required Command Groups

### Core runtime

- `bot run`: start continuous runtime.
- `bot stop`: stop a managed runtime if process control is implemented.
- `bot status`: show current runtime status, positions, balances, and health.

### Configuration and setup

- `bot doctor`: validate environment, config, and exchange connectivity.
- `bot config show`: display active non-secret configuration.
- `bot config validate`: validate the loaded configuration.
- `bot email set`: set or update the alert email recipient.
- `bot email test`: send a test email.

### Data

- `bot data import`: import local Kraken historical data packages.
- `bot data sync`: fetch missing or newer market data.
- `bot data check`: run integrity checks and gap reports.
- `bot data complete`: repair historical gaps and extend canonical data to the latest closed interval.
- `bot data source`: show source coverage and fallback usage.

### Research and models

- `bot features build`: build derived features.
- `bot model train`: train the ML model.
- `bot model validate`: run validation for a model candidate.
- `bot model promote`: promote a model artifact after validation gates pass.

### `bot features build`

This command must:

- read canonical Kraken daily candles for the selected assets
- generate deterministic features and labels without future leakage in feature columns
- reuse cached datasets when the deterministic `dataset_id` already exists unless a force rebuild is requested
- write the dataset and manifest under `artifacts/features/<dataset_id>/`
- prepare a matching experiment root under `artifacts/experiments/<dataset_id>/`

### Backtesting and simulation

- `bot backtest run`: execute a backtest.
- `bot backtest report`: view or export backtest results.
- `bot run --mode simulate`: start continuous simulation mode.

### `bot data complete`

This command must:

- inspect the selected canonical candle files for historical gaps and stale tails
- revisit previously fallback-filled or synthetic candles and replace them with Kraken-native candles when Kraken later serves that interval
- fetch missing Kraken candles first for each unresolved interval window
- use Binance and Coinbase only as documented fallback sources when Kraken cannot close a gap
- optionally apply an explicit synthetic carry-forward candle only as a last resort so the canonical series becomes continuous to the latest closed interval
- emit progress logs with completed range count, remaining range count, and ETA based on observed per-range throughput
- write a machine-readable completion summary under `artifacts/reports/data/latest_completion_summary.json`

### `bot backtest run`

This command must:

- build or reuse the deterministic feature dataset for the selected assets
- run a Kraken-only daily bar backtest using canonical `1d` candles
- generate order intents, simulated fills, and portfolio accounting from shared backtest models
- write run artifacts under `artifacts/backtests/<run_id>/`
- update `artifacts/reports/backtests/latest_backtest_report.json`

### `bot backtest report`

This command must:

- print the latest backtest report by default
- support loading a specific `run_id` when provided
- return a non-zero exit path if the requested report does not exist

### `bot run --mode simulate`

This command must:

- reuse the same target-weight and simulated execution path as the backtest service wherever practical
- load the latest persisted simulated portfolio state from `runtime/state/simulate_state.json`
- update that state after each completed simulation cycle
- return a clear waiting state when canonical data or deterministic signals are not yet available

### Live trading and monitoring

- `bot run --mode live`: start continuous live trading and terminal monitoring.

Live trading and monitoring are intentionally one runtime surface in V1.
The terminal during live mode must display monitoring information directly.

### Reporting and maintenance

- `bot report list`: list generated reports and artifacts.
- `bot report export`: export a chosen report.
- `bot logs tail`: tail recent structured logs in a readable form.

## Command Behavior Requirements

### `bot run`

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

### Monitoring output during `bot run`

The terminal display must include at minimum:

- mode
- exchange connectivity state
- latest decision timestamp
- current holdings and cash
- latest model and regime summary
- recent order activity
- freeze status
- alert-worthy incidents

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