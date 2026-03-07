"""CLI entrypoints for the trading bot."""

import typer

from spotbot import __version__
from spotbot.config import load_config

app = typer.Typer(help="CLI for the crypto spot trading bot.")


@app.command("version")
def version() -> None:
    """Print the current application version."""
    typer.echo(__version__)


@app.command("config-path")
def config_path() -> None:
    """Print the resolved configuration path."""
    config = load_config()
    typer.echo(str(config.config_path))