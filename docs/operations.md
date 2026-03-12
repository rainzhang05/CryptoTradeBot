# Operations Specification

## Operations Goals

- safe continuous runtime
- clear terminal observability
- useful alerts without manual babysitting
- restart-safe behavior
- clean path from local use to Dockerized deployment

## Runtime Environment

### Initial target

- macOS local machine
- interactive terminal shell for operator workflows

### Portability target

- Docker-based runtime suitable for future cloud deployment

## Operating Modes

### Simulate

- uses no real money
- produces runtime logs, reports, and monitoring output
- should mimic live behavior closely except for real exchange execution
- should resume from the last persisted simulated portfolio state when that state exists

### Live

- uses real Kraken account balances and orders
- continuously displays monitoring information in the terminal
- sends alerts on important events

Phase 7 implements the live terminal monitoring surface and persisted live-status report.
Email alert delivery remains part of the later observability phase.

## Monitoring Requirements

During long-running execution, the terminal monitoring surface must display at minimum:

- current mode
- exchange connection health
- current portfolio
- USD cash balance
- most recent decision outcome
- open orders
- recent fills
- warnings, freezes, and abnormal conditions

## Logging Requirements

The project must implement comprehensive logs.

### Logging expectations

- structured logs by default
- event timestamps in UTC
- clear event types for decisions, orders, fills, alerts, and failures
- separate human-readable terminal monitoring from durable log storage
- no secret leakage in logs

Phase 8 writes durable application logs to `runtime/logs/cryptotradebot.log` in JSON-line format so CLI inspection commands can tail recent activity.
Phase 9 also persists the latest runtime context under `runtime/state/runtime_context.json` and writes operator-facing runtime status reports under `artifacts/reports/runtime/latest_runtime_context.json`.

## Alerting Requirements

Alerts must be sent to:

- terminal
- email

### Required alert classes

- trade executed
- stop or forced reduction triggered if implemented
- freeze triggered
- kill-switch or catastrophe state entered if implemented
- exchange or API failure
- data gap or data-integrity failure
- portfolio drawdown threshold events
- startup failure

Phase 9 persists alert-deduplication state under `runtime/state/alert_state.json` and writes the latest operator-facing alert history under `artifacts/reports/runtime/latest_alerts.json`.
Terminal monitoring must render newly emitted alerts immediately, and email delivery must avoid repeating the same alert condition every cycle.

## Email Configuration

The CLI must allow the operator to set the email recipient.
Email delivery credentials, if needed, are secrets and must be loaded through `.env`.

## Interactive Shell Requirements

Phase 11 introduces an interactive operator shell launched by bare `cryptotradebot` on interactive terminals.

The shell must:

- present operator context such as active home, config path, runtime mode, and command state
- render structured command progress and summaries in readable transcript form
- keep the input surface locked while a long-running command is active
- treat `Ctrl-C` as a shell-exit shortcut with a confirmation guard: the first press must log an instruction to press `Ctrl-C` again, and the second press within 5 seconds must exit the shell
- keep durable JSON logs and operator-facing reports as the system of record beneath the hood

The direct command mode remains required for automation and scripts.

## Freeze Policy

Automatic freeze behavior is required.

The runtime must freeze new trading activity when any of the following occurs:

- required market data is missing or stale
- exchange connectivity is unreliable
- order placement failures repeat beyond acceptable tolerance
- account reconciliation fails
- configuration is invalid for the requested mode

When frozen, the system must:

- stop placing new orders
- clearly log and display the freeze reason
- emit an alert
- preserve enough state for investigation and recovery

Phase 7 persists freeze and live-account state in `runtime/state/live_state.json` and writes the latest cycle summary to `artifacts/reports/runtime/latest_live_status.json`.
Phase 9 persists runtime context, alert state, and the latest alert history so freeze investigation can continue after restart.

## Restart and Recovery Requirements

After restart, the system must be able to:

- reload latest persisted state
- resync balances and open orders from Kraken
- detect mismatches between expected and actual account state
- resume safely only after consistency checks pass

For simulate mode, the minimum persisted state includes the last simulated cash balance, open simulated positions, and the most recent decision timestamp.
Phase 8 also tracks the active foreground runtime process in `runtime/state/runtime_process.json` so status inspection and managed termination are possible from the CLI.

## Runbook Requirements

The final project must include operator runbooks for:

- initial setup
- historical data import
- simulate mode operation
- live mode preflight
- live mode operation
- freeze recovery
- incident investigation
- release validation

These procedures are maintained in `docs/runbooks.md`.

## Deployment Expectations

- Docker is the required deployment format.
- Local operation is the initial standard deployment.
- Future cloud deployment should require minimal architectural change.
- Global operator installation must be supported through a published Python package installable via `pipx`.
