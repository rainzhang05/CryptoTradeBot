"""CLI entrypoints for the trading bot."""

import json
from typing import Any, cast

import typer

from spotbot import __version__
from spotbot.config import load_config
from spotbot.data.service import DataService
from spotbot.logging_config import configure_logging
from spotbot.runtime import RuntimeService

app = typer.Typer(help="CLI for the crypto spot trading bot.")
config_app = typer.Typer(help="Inspect and validate non-secret configuration.")
data_app = typer.Typer(help="Import, inspect, and validate local market data.")
ASSETS_OPTION = typer.Option(default=None)

app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")


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