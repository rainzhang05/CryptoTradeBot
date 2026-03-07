"""Integration tests for the data CLI commands."""

import shutil
from pathlib import Path

from typer.testing import CliRunner

from tradebot.cli import app

runner = CliRunner()


def test_data_import_and_check_commands(tmp_path: Path, monkeypatch) -> None:
    raw_dir = tmp_path / "data" / "kraken_data"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(__file__).parents[2] / "fixtures" / "raw" / "kraken"
    shutil.copy(fixture_dir / "XBTUSD.csv", raw_dir / "XBTUSD.csv")

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
data:
  raw_kraken_dir: data/kraken_data
  canonical_dir: data/canonical
  reports_dir: artifacts/reports/data
  intervals: [1h, 1d]
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

    import_result = runner.invoke(app, ["data", "import", "--assets", "BTC"])
    check_result = runner.invoke(app, ["data", "check", "--assets", "BTC"])

    assert import_result.exit_code == 0
    assert '"asset": "BTC"' in import_result.stdout
    assert check_result.exit_code == 0
    assert '"interval": "1h"' in check_result.stdout


def test_data_source_command(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
data:
  raw_kraken_dir: data/kraken_data
  canonical_dir: data/canonical
  reports_dir: artifacts/reports/data
  intervals: [1h, 1d]
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

    result = runner.invoke(app, ["data", "source"])

    assert result.exit_code == 0
    assert '"asset": "BTC"' in result.stdout