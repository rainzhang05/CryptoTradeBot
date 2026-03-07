"""CLI entrypoints for the trading bot."""

import json
from typing import Any, cast

import typer

from tradebot import __version__
from tradebot.backtest.service import BacktestService
from tradebot.config import load_config
from tradebot.data.service import DataService
from tradebot.logging_config import configure_logging
from tradebot.research.service import ResearchService
from tradebot.runtime import RuntimeService

app = typer.Typer(help="CLI for the crypto spot trading bot.")
config_app = typer.Typer(help="Inspect and validate non-secret configuration.")
data_app = typer.Typer(help="Import, inspect, and validate local market data.")
features_app = typer.Typer(help="Build deterministic research datasets.")
backtest_app = typer.Typer(help="Run historical backtests and inspect reports.")
ASSETS_OPTION = typer.Option(default=None)

app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")
app.add_typer(features_app, name="features")
app.add_typer(backtest_app, name="backtest")


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
    """Run a lightweight preflight summary for the local environment."""
    config = load_config()
    paths = config.resolved_paths()
    summary = {
        "config_path": str(config.config_path),
        "project_root": str(config.project_root),
        "exchange": config.exchange.name,
        "base_currency": config.exchange.base_currency,
        "default_mode": config.runtime.default_mode,
        "log_format": config.app.log_format,
        "email_configured": bool(config.alerts.email_recipient),
        "paths": {
            "data_dir": str(paths.data_dir),
            "artifacts_dir": str(paths.artifacts_dir),
            "features_dir": str(paths.features_dir),
            "experiments_dir": str(paths.experiments_dir),
            "logs_dir": str(paths.logs_dir),
            "state_dir": str(paths.state_dir),
        },
    }
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


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
    mode: str | None = typer.Option(default=None, help="Runtime mode: simulate or live."),
    max_cycles: int | None = typer.Option(
        default=None,
        min=1,
        help="Optional cycle count override for testing or short runs.",
    ),
) -> None:
    """Start the runtime skeleton for simulate or live mode."""
    config = load_config()
    configure_logging(config)
    runtime = RuntimeService(config)
    effective_mode = mode or config.runtime.default_mode
    snapshots = runtime.run(mode=effective_mode, max_cycles=max_cycles)
    typer.echo(f"Completed {len(snapshots)} cycle(s) in {effective_mode} mode.")


def sanitized_config(config: Any) -> dict[str, Any]:
    """Return a configuration payload without secret values."""
    payload = cast(dict[str, Any], config.model_dump(mode="json", exclude={"secrets"}))
    payload["secrets_present"] = {
        "kraken_api_key": bool(config.secrets.kraken_api_key),
        "kraken_api_secret": bool(config.secrets.kraken_api_secret),
        "smtp_host": bool(config.secrets.smtp_host),
        "smtp_username": bool(config.secrets.smtp_username),
        "smtp_password": bool(config.secrets.smtp_password),
    }
    return payload


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