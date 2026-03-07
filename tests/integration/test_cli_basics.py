"""Integration tests for the CLI skeleton."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from spotbot.cli import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_config_path_command(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

    result = runner.invoke(app, ["config-path"])

    assert result.exit_code == 0
    assert str(config_path.resolve()) in result.stdout


def test_config_validate_command(tmp_path: Path, monkeypatch) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "settings.yaml"
        config_path.write_text(
                """
app: {}
runtime: {}
exchange: {}
strategy:
    fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
                encoding="utf-8",
        )
        monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

        result = runner.invoke(app, ["config", "validate"])

        assert result.exit_code == 0
        assert "Configuration valid" in result.stdout


def test_run_command(tmp_path: Path, monkeypatch) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "settings.yaml"
        config_path.write_text(
                """
app:
    log_format: console
runtime:
    default_mode: simulate
    max_cycles: 1
exchange: {}
strategy:
    fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
                encoding="utf-8",
        )
        monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

        result = runner.invoke(app, ["run", "--mode", "simulate", "--max-cycles", "1"])

        assert result.exit_code == 0
        assert "Completed 1 cycle(s) in simulate mode." in result.stdout