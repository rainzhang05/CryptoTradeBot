# Interactive Shell Commands

This guide is for operators using the interactive `tradebot` shell.
Inside the shell, type commands without the `tradebot` prefix.
When a command needs more input, the shell opens a form so you can fill in the values instead of remembering flags.

## Shell Basics

- `help`: show the shell help summary.
- `clear`: clear the transcript and keep the current shell session open.
- `exit`: close the shell immediately.
- `Ctrl+C`: press once to see the exit warning, then press again within 5 seconds to close the shell.

## Setup And Health

- `init`: create or refresh the default Tradebot home under `~/.tradebot/`.
- `version`: print the installed application version.
- `config-path`: show the resolved active configuration path.
- `doctor`: check your environment, configuration, and Kraken connectivity.
- `status`: show the latest runtime, portfolio, and health status.

## Runtime

- `run`: start the shared runtime. In the shell form, choose `simulate` or `live`.
- `stop`: request termination of a tracked runtime process.

## Configuration And Alerts

- `config show`: display the active non-secret configuration.
- `config validate`: validate the current configuration file.
- `email set`: save the alert email recipient.
- `email test`: send a test email with the configured SMTP settings.

## Data

- `data source`: show raw and canonical source coverage.
- `data import`: import local Kraken historical files.
- `data sync`: fetch newer or missing market data.
- `data check`: run canonical data integrity checks.
- `data complete`: repair gaps and extend canonical data to the latest closed interval.
- `data prune-raw`: delete unsupported raw Kraken CSV files outside the fixed V1 universe.

## Research And Models

- `features build`: build the deterministic feature dataset. The shell form exposes the dataset track.
- `model train`: train the current model candidate. The shell form exposes the dataset track and model family.
- `model validate`: validate a trained model.
- `model promote`: promote a validated model for runtime and backtests.

## Backtests And Reports

- `backtest run`: execute a backtest using the current configuration. The shell form exposes the dataset track, optional model id, and active-model usage.
- `backtest report`: open the latest or a selected backtest report.
- `report list`: list saved reports and artifacts.
- `report export`: copy one saved artifact to another path.
- `logs tail`: show recent durable logs in readable form.
