"""Unit tests for data service import behavior."""

from pathlib import Path
import shutil

from spotbot.config import load_config
from spotbot.data.service import DataService


def test_import_kraken_raw_creates_canonical_files(tmp_path: Path) -> None:
    raw_dir = tmp_path / "data" / "kraken_data"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(__file__).parents[2] / "fixtures" / "raw" / "kraken"
    shutil.copy(fixture_dir / "XBTUSD.csv", raw_dir / "XBTUSD.csv")
    shutil.copy(fixture_dir / "ETHUSD.csv", raw_dir / "ETHUSD.csv")

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
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")

    summary = DataService(config).import_kraken_raw(assets=("BTC", "ETH"))

    assert len(summary.assets) == 2
    assert (tmp_path / "data" / "canonical" / "kraken" / "BTC" / "candles_1h.csv").exists()
    assert (tmp_path / "data" / "canonical" / "kraken" / "ETH" / "manifest.json").exists()
    assert Path(summary.report_file).exists()