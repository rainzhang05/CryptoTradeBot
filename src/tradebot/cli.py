"""CLI entrypoints for the trading bot."""

import json
from pathlib import Path
from typing import Any, cast

import typer

from tradebot import __version__
from tradebot.backtest.service import BacktestService
from tradebot.config import load_config
from tradebot.data.service import DataService
from tradebot.logging_config import configure_logging
from tradebot.model.service import ModelService
from tradebot.operations import OperationsService
from tradebot.research.service import ResearchService
from tradebot.runtime import RuntimeService

app = typer.Typer(help="CLI for the crypto spot trading bot.")
config_app = typer.Typer(help="Inspect and validate non-secret configuration.")
data_app = typer.Typer(help="Import, inspect, and validate local market data.")
features_app = typer.Typer(help="Build deterministic research datasets.")
model_app = typer.Typer(help="Train, validate, and promote ML model artifacts.")
backtest_app = typer.Typer(help="Run historical backtests and inspect reports.")
email_app = typer.Typer(help="Manage alert email configuration and SMTP checks.")
report_app = typer.Typer(help="List and export generated reports and artifacts.")
logs_app = typer.Typer(help="Inspect durable application logs.")
ASSETS_OPTION = typer.Option(default=None)

app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")
app.add_typer(features_app, name="features")
app.add_typer(model_app, name="model")
app.add_typer(backtest_app, name="backtest")
app.add_typer(email_app, name="email")
app.add_typer(report_app, name="report")
app.add_typer(logs_app, name="logs")


@app.command("version")
def version() -> None:
    """Print the current application version."""
    typer.echo(__version__)


@app.command("config-path")
def config_path() -> None:
    """Print the resolved configuration path."""
    config = load_config()
    typer.echo(str(config.config_path))


@app.command("doctor")
def doctor() -> None:
    """Validate config, local environment, and exchange connectivity."""
    config = load_config()
    summary = OperationsService(config).doctor_summary()
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    if not bool(summary["ok"]):
        raise typer.Exit(code=1)


@config_app.command("show")
def config_show() -> None:
    """Print the active non-secret configuration."""
    config = load_config()
    payload = sanitized_config(config)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@config_app.command("validate")
def config_validate() -> None:
    """Validate the active configuration and print a short success message."""
    config = load_config()
    typer.echo(f"Configuration valid: {config.config_path}")


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
) -> None:
    """Start the shared simulate or live runtime loop."""
    config = load_config()
    configure_logging(config)
    runtime = RuntimeService(config)
    effective_mode = mode or config.runtime.default_mode
    try:
        snapshots = runtime.run(
            mode=effective_mode,
            max_cycles=max_cycles,
            on_cycle=lambda snapshot: typer.echo(render_runtime_snapshot(snapshot)),
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Completed {len(snapshots)} cycle(s) in {effective_mode} mode.")


@app.command("stop")
def stop() -> None:
    """Stop a managed runtime process when one is active."""
    config = load_config()
    service = OperationsService(config)
    try:
        summary = service.stop_runtime()
    except (FileNotFoundError, ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@app.command("status")
def status() -> None:
    """Show the latest known runtime status, positions, balances, and health."""
    config = load_config()
    summary = OperationsService(config).runtime_status()
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


def sanitized_config(config: Any) -> dict[str, Any]:
    """Return a configuration payload without secret values."""
    payload = cast(dict[str, Any], config.model_dump(mode="json", exclude={"secrets"}))
    payload["secrets_present"] = {
        "kraken_api_key": bool(config.secrets.kraken_api_key),
        "kraken_api_secret": bool(config.secrets.kraken_api_secret),
        "kraken_api_otp": bool(config.secrets.kraken_api_otp),
        "smtp_host": bool(config.secrets.smtp_host),
        "smtp_username": bool(config.secrets.smtp_username),
        "smtp_password": bool(config.secrets.smtp_password),
    }
    return payload


def render_runtime_snapshot(snapshot: Any) -> str:
    """Render one runtime-cycle summary for terminal monitoring output."""
    timestamp = "n/a" if snapshot.timestamp is None else str(snapshot.timestamp)
    equity = "n/a" if snapshot.equity_usd is None else f"{snapshot.equity_usd:.2f}"
    cash = "n/a" if snapshot.cash_usd is None else f"{snapshot.cash_usd:.2f}"
    holdings = ", ".join(
        f"{asset}:{quantity:.8f}"
        for asset, quantity in sorted(snapshot.holdings.items())
    )
    incidents = ", ".join(snapshot.incidents)
    return " | ".join(
        [
            f"mode={snapshot.mode}",
            f"cycle={snapshot.cycle}",
            f"status={snapshot.status}",
            f"system={snapshot.system_status}",
            f"connectivity={snapshot.connectivity_state}",
            f"timestamp={timestamp}",
            f"regime={snapshot.regime_state or 'n/a'}",
            f"risk={snapshot.risk_state or 'n/a'}",
            f"equity_usd={equity}",
            f"cash_usd={cash}",
            f"holdings={holdings or 'none'}",
            f"fills={snapshot.fill_count}",
            f"open_orders={snapshot.open_order_count}",
            f"model={snapshot.model_id or 'n/a'}",
            f"decision_executed={'yes' if snapshot.decision_executed else 'no'}",
            f"freeze={snapshot.freeze_reason or 'none'}",
            f"incidents={incidents or 'none'}",
        ]
    )


@email_app.command("set")
def email_set(recipient: str = typer.Argument(..., help="Alert email recipient.")) -> None:
    """Set or update the configured alert email recipient."""
    config = load_config()
    try:
        summary = OperationsService(config).set_email_recipient(recipient)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@email_app.command("test")
def email_test(
    recipient: str | None = typer.Option(
        default=None,
        help="Optional override recipient. Defaults to the configured alert recipient.",
    ),
) -> None:
    """Send a test email using the configured SMTP settings."""
    config = load_config()
    try:
        summary = OperationsService(config).send_test_email(recipient=recipient)
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@report_app.command("list")
def report_list() -> None:
    """List stored reports and artifacts under the project artifacts directory."""
    config = load_config()
    entries = OperationsService(config).list_reports()
    typer.echo(json.dumps(entries, indent=2, sort_keys=True))


@report_app.command("export")
def report_export(
    source: str = typer.Argument(..., help="Source report or artifact path."),
    destination: str = typer.Argument(..., help="Destination file path."),
) -> None:
    """Export one stored report or artifact to a chosen destination."""
    config = load_config()
    try:
        summary = OperationsService(config).export_report(source, Path(destination))
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@logs_app.command("tail")
def logs_tail(
    lines: int = typer.Option(
        default=50,
        min=1,
        help="Number of recent log lines to render.",
    ),
) -> None:
    """Tail recent durable logs in a readable format."""
    config = load_config()
    try:
        rendered_lines = OperationsService(config).tail_logs(lines=lines)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    for line in rendered_lines:
        typer.echo(line)


@data_app.command("import")
def data_import(assets: list[str] | None = ASSETS_OPTION) -> None:
    """Import raw Kraken trade files into canonical candles."""
    config = load_config()
    service = DataService(config)
    summary = service.import_kraken_raw(assets=tuple(assets) if assets else None)
    typer.echo(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


@data_app.command("check")
def data_check(assets: list[str] | None = ASSETS_OPTION) -> None:
    """Validate canonical Kraken candles and emit an integrity report."""
    config = load_config()
    service = DataService(config)
    summary = service.check_canonical(assets=tuple(assets) if assets else None)
    typer.echo(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


@data_app.command("source")
def data_source() -> None:
    """Show raw and canonical source coverage for the fixed-universe assets."""
    config = load_config()
    service = DataService(config)
    typer.echo(json.dumps(service.source_summary(), indent=2, sort_keys=True))


@data_app.command("sync")
def data_sync(assets: list[str] | None = ASSETS_OPTION) -> None:
    """Extend canonical candles using public exchange APIs."""
    config = load_config()
    service = DataService(config)
    summary = service.sync_canonical(assets=tuple(assets) if assets else None)
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


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
    config = load_config()
    configure_logging(config)
    service = DataService(config)
    summary = service.complete_canonical(
        assets=tuple(assets) if assets else None,
        allow_synthetic=allow_synthetic,
    )
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@data_app.command("prune-raw")
def data_prune_raw() -> None:
    """Delete raw Kraken files that are outside the fixed V1 universe."""
    config = load_config()
    service = DataService(config)
    summary = service.prune_raw_kraken()
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@features_app.command("build")
def features_build(
    assets: list[str] | None = ASSETS_OPTION,
    force: bool = typer.Option(
        default=False,
        help="Rebuild the dataset even if the deterministic cache already exists.",
    ),
) -> None:
    """Build a deterministic feature and label dataset from canonical daily candles."""
    config = load_config()
    service = ResearchService(config)
    summary = service.build_feature_store(assets=tuple(assets) if assets else None, force=force)
    typer.echo(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


@model_app.command("train")
def model_train(
    assets: list[str] | None = ASSETS_OPTION,
    force_features: bool = typer.Option(
        default=False,
        help="Rebuild the feature dataset before training the model.",
    ),
) -> None:
    """Train the Phase 6 ML artifact with walk-forward validation."""
    config = load_config()
    service = ModelService(config)
    try:
        summary = service.train_model(
            assets=tuple(assets) if assets else None,
            force_features=force_features,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


@model_app.command("validate")
def model_validate(
    model_id: str | None = typer.Option(
        default=None,
        help="Optional model identifier. Defaults to the latest trained model.",
    ),
) -> None:
    """Validate one trained model artifact against the promotion rules."""
    config = load_config()
    service = ModelService(config)
    summary = service.validate_model(model_id=model_id)
    typer.echo(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


@model_app.command("promote")
def model_promote(
    model_id: str | None = typer.Option(
        default=None,
        help="Optional model identifier. Defaults to the latest trained model.",
    ),
) -> None:
    """Promote one validated model artifact to the active strategy pointer."""
    config = load_config()
    service = ModelService(config)
    summary = service.promote_model(model_id=model_id)
    typer.echo(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


@backtest_app.command("run")
def backtest_run(
    assets: list[str] | None = ASSETS_OPTION,
    force_features: bool = typer.Option(
        default=False,
        help="Rebuild the feature dataset before running the backtest.",
    ),
) -> None:
    """Execute a reproducible Kraken-only backtest on canonical daily data."""
    config = load_config()
    service = BacktestService(config)
    summary = service.run_backtest(
        assets=tuple(assets) if assets else None,
        force_features=force_features,
    )
    typer.echo(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


@backtest_app.command("report")
def backtest_report(
    run_id: str | None = typer.Option(
        default=None,
        help="Optional run identifier. Defaults to the latest backtest report.",
    ),
) -> None:
    """Print a stored backtest report."""
    config = load_config()
    service = BacktestService(config)
    report = service.load_backtest_report(run_id=run_id)
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
