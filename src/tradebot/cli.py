"""CLI entrypoints for the trading bot."""

from __future__ import annotations

import sys
from typing import Any

import typer

from tradebot.commanding import (
    RuntimeRunResult,
    execute_command,
    render_alert_event,
    render_direct_output,
    render_runtime_snapshot,
)
from tradebot.config import ConfigError
from tradebot.runtime import RuntimeSnapshot

app = typer.Typer(help="CLI for the crypto spot trading bot.")
config_app = typer.Typer(help="Inspect and validate non-secret configuration.")
data_app = typer.Typer(help="Import, inspect, and validate local market data.")
features_app = typer.Typer(help="Build deterministic research datasets.")
research_app = typer.Typer(help="Run staged research sweeps and inspect reports.")
model_app = typer.Typer(help="Train, validate, and promote ML model artifacts.")
backtest_app = typer.Typer(help="Run historical backtests and inspect reports.")
email_app = typer.Typer(help="Manage alert email configuration and SMTP checks.")
report_app = typer.Typer(help="List and export generated reports and artifacts.")
logs_app = typer.Typer(help="Inspect durable application logs.")
ASSETS_OPTION = typer.Option(default=None)

app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")
app.add_typer(features_app, name="features")
app.add_typer(research_app, name="research")
app.add_typer(model_app, name="model")
app.add_typer(backtest_app, name="backtest")
app.add_typer(email_app, name="email")
app.add_typer(report_app, name="report")
app.add_typer(logs_app, name="logs")


def _invoke_direct(
    command_id: str,
    params: dict[str, object] | None = None,
    *,
    emitter: Any | None = None,
) -> object:
    try:
        return execute_command(command_id, params=params, emitter=emitter)
    except (ConfigError, FileNotFoundError, ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _runtime_emitter(event: Any) -> None:
    if event.kind == "runtime_snapshot":
        payload = event.payload
        snapshot = RuntimeSnapshot(**payload)
        typer.echo(render_runtime_snapshot(snapshot))
        return
    if event.kind == "alert":
        payload = event.payload
        alert = type("ShellAlert", (), payload)
        typer.echo(render_alert_event(alert))


def launch_shell() -> None:
    """Launch the interactive Tradebot shell."""
    from tradebot.shell import TradebotShellApp

    TradebotShellApp().run()


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def main(argv: list[str] | None = None) -> None:
    """Route the console entrypoint between shell mode and direct CLI mode."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        if _is_interactive_terminal():
            launch_shell()
            return
        app(prog_name="tradebot", args=["--help"])
        return
    app(prog_name="tradebot", args=args)


@app.command("version")
def version() -> None:
    """Print the current application version."""
    typer.echo(render_direct_output("version", _invoke_direct("version")))


@app.command("config-path")
def config_path() -> None:
    """Print the resolved configuration path."""
    typer.echo(render_direct_output("config_path", _invoke_direct("config_path")))


@app.command("init")
def init(
    home: str | None = typer.Option(
        default=None,
        help="Optional application-home override.",
    ),
    force: bool = typer.Option(
        default=False,
        help="Rewrite starter config and env files when they already exist.",
    ),
) -> None:
    """Bootstrap the default application home and starter files."""
    payload = _invoke_direct("init", {"home": home, "force": force})
    typer.echo(render_direct_output("init", payload))


@app.command("shell")
def shell_command() -> None:
    """Launch the interactive operator shell explicitly."""
    launch_shell()


@app.command("doctor")
def doctor() -> None:
    """Validate config, local environment, and exchange connectivity."""
    payload = _invoke_direct("doctor")
    typer.echo(render_direct_output("doctor", payload))
    if isinstance(payload, dict) and not bool(payload["ok"]):
        raise typer.Exit(code=1)


@config_app.command("show")
def config_show() -> None:
    """Print the active non-secret configuration."""
    typer.echo(render_direct_output("config_show", _invoke_direct("config_show")))


@config_app.command("validate")
def config_validate() -> None:
    """Validate the active configuration and print a short success message."""
    typer.echo(render_direct_output("config_validate", _invoke_direct("config_validate")))


@app.command("run")
def run(
    mode: str | None = typer.Option(
        default=None,
        help="Runtime mode to execute.",
    ),
    max_cycles: int | None = typer.Option(
        default=None,
        min=1,
        help="Optional cycle count override for testing or short runs.",
    ),
    dataset_track: str | None = typer.Option(
        default=None,
        help="Optional research/backtest dataset track override.",
    ),
) -> None:
    """Start the shared simulate or live runtime loop."""
    payload = _invoke_direct(
        "run",
        {"mode": mode, "max_cycles": max_cycles, "dataset_track": dataset_track},
        emitter=_runtime_emitter,
    )
    if not isinstance(payload, RuntimeRunResult):
        raise typer.Exit(code=1)
    typer.echo(render_direct_output("run", payload))


@app.command("stop")
def stop() -> None:
    """Stop a managed runtime process when one is active."""
    typer.echo(render_direct_output("stop", _invoke_direct("stop")))


@app.command("status")
def status() -> None:
    """Show the latest known runtime status, positions, balances, and health."""
    typer.echo(render_direct_output("status", _invoke_direct("status")))


@email_app.command("set")
def email_set(recipient: str = typer.Argument(..., help="Alert email recipient.")) -> None:
    """Set or update the configured alert email recipient."""
    typer.echo(
        render_direct_output(
            "email_set",
            _invoke_direct("email_set", {"recipient": recipient}),
        )
    )


@email_app.command("test")
def email_test(
    recipient: str | None = typer.Option(
        default=None,
        help="Optional override recipient. Defaults to the configured alert recipient.",
    ),
) -> None:
    """Send a test email using the configured SMTP settings."""
    typer.echo(
        render_direct_output(
            "email_test",
            _invoke_direct("email_test", {"recipient": recipient}),
        )
    )


@report_app.command("list")
def report_list() -> None:
    """List stored reports and artifacts under the project artifacts directory."""
    typer.echo(render_direct_output("report_list", _invoke_direct("report_list")))


@report_app.command("export")
def report_export(
    source: str = typer.Argument(..., help="Source report or artifact path."),
    destination: str = typer.Argument(..., help="Destination file path."),
) -> None:
    """Export one stored report or artifact to a chosen destination."""
    typer.echo(
        render_direct_output(
            "report_export",
            _invoke_direct(
                "report_export",
                {"source": source, "destination": destination},
            ),
        )
    )


@logs_app.command("tail")
def logs_tail(
    lines: int = typer.Option(
        default=50,
        min=1,
        help="Number of recent log lines to render.",
    ),
) -> None:
    """Tail recent durable logs in a readable format."""
    typer.echo(
        render_direct_output("logs_tail", _invoke_direct("logs_tail", {"lines": lines}))
    )


@data_app.command("import")
def data_import(assets: list[str] | None = ASSETS_OPTION) -> None:
    """Import raw Kraken trade files into canonical candles."""
    typer.echo(
        render_direct_output("data_import", _invoke_direct("data_import", {"assets": assets}))
    )


@data_app.command("check")
def data_check(assets: list[str] | None = ASSETS_OPTION) -> None:
    """Validate canonical Kraken candles and emit an integrity report."""
    typer.echo(
        render_direct_output("data_check", _invoke_direct("data_check", {"assets": assets}))
    )


@data_app.command("source")
def data_source() -> None:
    """Show raw and canonical source coverage for the fixed-universe assets."""
    typer.echo(render_direct_output("data_source", _invoke_direct("data_source")))


@data_app.command("sync")
def data_sync(assets: list[str] | None = ASSETS_OPTION) -> None:
    """Extend canonical candles using public exchange APIs."""
    typer.echo(
        render_direct_output("data_sync", _invoke_direct("data_sync", {"assets": assets}))
    )


@data_app.command("complete")
def data_complete(
    assets: list[str] | None = ASSETS_OPTION,
    allow_synthetic: bool = typer.Option(
        default=True,
        help=(
            "Use explicit synthetic carry-forward candles only when Kraken, "
            "Binance, and Coinbase still cannot close a gap."
        ),
    ),
) -> None:
    """Fill canonical gaps and extend all selected series to the latest closed interval."""
    typer.echo(
        render_direct_output(
            "data_complete",
            _invoke_direct(
                "data_complete",
                {"assets": assets, "allow_synthetic": allow_synthetic},
            ),
        )
    )


@data_app.command("prune-raw")
def data_prune_raw() -> None:
    """Delete raw Kraken files that are outside the fixed V1 universe."""
    typer.echo(render_direct_output("data_prune_raw", _invoke_direct("data_prune_raw")))


@features_app.command("build")
def features_build(
    assets: list[str] | None = ASSETS_OPTION,
    force: bool = typer.Option(
        default=False,
        help="Rebuild the dataset even if the deterministic cache already exists.",
    ),
    dataset_track: str | None = typer.Option(
        default=None,
        help="Optional dataset track override.",
    ),
) -> None:
    """Build a deterministic feature and label dataset from canonical daily candles."""
    typer.echo(
        render_direct_output(
            "features_build",
            _invoke_direct(
                "features_build",
                {"assets": assets, "force": force, "dataset_track": dataset_track},
            ),
        )
    )


@research_app.command("sweep")
def research_sweep(
    preset: str = typer.Option(
        default="broad_staged",
        help="Named staged research sweep preset to execute.",
    ),
    resume: bool = typer.Option(
        default=False,
        help="Resume a previously started deterministic sweep id for this preset/config.",
    ),
    max_workers: int = typer.Option(
        default=1,
        min=1,
        help="Requested research worker count.",
    ),
    limit: int | None = typer.Option(
        default=None,
        min=1,
        help="Optional cap on the number of experiments to execute this run.",
    ),
) -> None:
    """Execute a staged research sweep across dataset, rule, and ML combinations."""
    typer.echo(
        render_direct_output(
            "research_sweep",
            _invoke_direct(
                "research_sweep",
                {
                    "preset": preset,
                    "resume": resume,
                    "max_workers": max_workers,
                    "limit": limit,
                },
            ),
        )
    )


@research_app.command("report")
def research_report(
    sweep_id: str | None = typer.Argument(
        default=None,
        help="Optional sweep identifier. Defaults to the latest generated sweep report.",
    ),
) -> None:
    """Print a stored research sweep report."""
    typer.echo(
        render_direct_output(
            "research_report",
            _invoke_direct("research_report", {"sweep_id": sweep_id}),
        )
    )


@model_app.command("train")
def model_train(
    assets: list[str] | None = ASSETS_OPTION,
    force_features: bool = typer.Option(
        default=False,
        help="Rebuild the feature dataset before training the model.",
    ),
    dataset_track: str | None = typer.Option(
        default=None,
        help="Optional dataset track override.",
    ),
    family: str = typer.Option(
        default="ridge_logistic",
        help="Model family to train.",
    ),
) -> None:
    """Train the Phase 6 ML artifact with walk-forward validation."""
    typer.echo(
        render_direct_output(
            "model_train",
            _invoke_direct(
                "model_train",
                {
                    "assets": assets,
                    "force_features": force_features,
                    "dataset_track": dataset_track,
                    "family": family,
                },
            ),
        )
    )


@model_app.command("validate")
def model_validate(
    model_id: str | None = typer.Option(
        default=None,
        help="Optional model identifier. Defaults to the latest trained model.",
    ),
) -> None:
    """Validate one trained model artifact against the promotion rules."""
    typer.echo(
        render_direct_output(
            "model_validate",
            _invoke_direct("model_validate", {"model_id": model_id}),
        )
    )


@model_app.command("promote")
def model_promote(
    model_id: str | None = typer.Option(
        default=None,
        help="Optional model identifier. Defaults to the latest trained model.",
    ),
) -> None:
    """Promote one validated model artifact to the active strategy pointer."""
    typer.echo(
        render_direct_output(
            "model_promote",
            _invoke_direct("model_promote", {"model_id": model_id}),
        )
    )


@backtest_app.command("run")
def backtest_run(
    assets: list[str] | None = ASSETS_OPTION,
    force_features: bool = typer.Option(
        default=False,
        help="Rebuild the feature dataset before running the backtest.",
    ),
    dataset_track: str | None = typer.Option(
        default=None,
        help="Optional dataset track override.",
    ),
    model_id: str | None = typer.Option(
        default=None,
        help="Optional explicit model artifact to use.",
    ),
    use_active_model: bool = typer.Option(
        True,
        "--use-active-model/--no-use-active-model",
        help="Use the promoted active model when no explicit model id is provided.",
    ),
) -> None:
    """Execute a reproducible Kraken-only backtest on canonical daily data."""
    typer.echo(
        render_direct_output(
            "backtest_run",
            _invoke_direct(
                "backtest_run",
                {
                    "assets": assets,
                    "force_features": force_features,
                    "dataset_track": dataset_track,
                    "model_id": model_id,
                    "use_active_model": use_active_model,
                },
            ),
        )
    )


@backtest_app.command("report")
def backtest_report(
    run_id: str | None = typer.Option(
        default=None,
        help="Optional run identifier. Defaults to the latest backtest report.",
    ),
) -> None:
    """Print a stored backtest report."""
    typer.echo(
        render_direct_output(
            "backtest_report",
            _invoke_direct("backtest_report", {"run_id": run_id}),
        )
    )
