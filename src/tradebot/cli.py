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
kraken_app = typer.Typer(help="Manage Kraken-specific operator workflows.")
kraken_auth_app = typer.Typer(help="Manage Kraken API credentials.")
data_app = typer.Typer(help="Import, inspect, and validate local market data.")
features_app = typer.Typer(help="Build deterministic research datasets.")
backtest_app = typer.Typer(help="Run historical backtests and inspect reports.")
email_app = typer.Typer(help="Manage alert email configuration and SMTP checks.")
report_app = typer.Typer(help="List and export generated reports and artifacts.")
logs_app = typer.Typer(help="Inspect durable application logs.")
ASSETS_OPTION = typer.Option(default=None)

app.add_typer(config_app, name="config")
app.add_typer(kraken_app, name="kraken")
kraken_app.add_typer(kraken_auth_app, name="auth")
app.add_typer(data_app, name="data")
app.add_typer(features_app, name="features")
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
    """Launch the interactive CryptoTradeBot shell."""
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
        app(prog_name="cryptotradebot", args=["--help"])
        return
    app(prog_name="cryptotradebot", args=args)


@app.command("version")
def version() -> None:
    """Print the current application version."""
    typer.echo(render_direct_output("version", _invoke_direct("version")))


@app.command("config-path")
def config_path() -> None:
    """Print the resolved configuration path."""
    typer.echo(render_direct_output("config_path", _invoke_direct("config_path")))


@app.command("setup")
def setup(
    home: str | None = typer.Option(
        default=None,
        help="Optional application-home override.",
    ),
    force: bool = typer.Option(
        default=False,
        help="Rewrite starter config and env files when they already exist.",
    ),
    assets: list[str] | None = ASSETS_OPTION,
) -> None:
    """Initialize the app home, prepare runtime-ready data, and run readiness checks."""
    payload = _invoke_direct("setup", {"home": home, "force": force, "assets": assets})
    typer.echo(render_direct_output("setup", payload))
    if isinstance(payload, dict) and not bool(payload["ok"]):
        raise typer.Exit(code=1)


@app.command("shell")
def shell_command() -> None:
    """Launch the interactive operator shell explicitly."""
    launch_shell()

@kraken_auth_app.command("set")
def kraken_auth_set(
    api_key: str = typer.Argument(..., help="Kraken API key."),
    secret: str | None = typer.Option(default=None, help="Optional Kraken API secret."),
    otp: str | None = typer.Option(default=None, help="Optional Kraken OTP value."),
) -> None:
    """Write Kraken credentials into the active environment file."""
    typer.echo(
        render_direct_output(
            "kraken_auth_set",
            _invoke_direct(
                "kraken_auth_set",
                {"api_key": api_key, "api_secret": secret, "otp": otp},
            ),
        )
    )


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
    strategy_preset: str | None = typer.Option(
        default=None,
        help="Optional strategy preset override.",
    ),
) -> None:
    """Start the shared simulate or live runtime loop."""
    payload = _invoke_direct(
        "run",
        {
            "mode": mode,
            "max_cycles": max_cycles,
            "dataset_track": dataset_track,
            "strategy_preset": strategy_preset,
        },
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
    """Build a deterministic feature dataset from canonical daily candles."""
    typer.echo(
        render_direct_output(
            "features_build",
            _invoke_direct(
                "features_build",
                {"assets": assets, "force": force, "dataset_track": dataset_track},
            ),
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
    strategy_preset: str | None = typer.Option(
        default=None,
        help="Optional strategy preset override.",
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
                    "strategy_preset": strategy_preset,
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
