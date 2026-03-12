"""Integration tests for the research CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tradebot.cli import app
from tradebot.data.models import Candle
from tradebot.data.storage import write_candles

runner = CliRunner()


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
data:
  canonical_dir: data/canonical
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
research:
  primary_interval: 1d
  momentum_windows_days: [2]
  trend_windows_days: [2, 4]
  volatility_windows_days: [2]
  relative_strength_window_days: 2
  breadth_window_days: 2
  dollar_volume_window_days: 2
  source_window_days: 2
  forward_return_days: 1
  downside_lookahead_days: 2
  downside_threshold: 0.05
  sell_lookahead_days: 3
  sell_drawdown_threshold: 0.08
  sell_return_threshold: -0.01
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    return config_path


def _write_daily_candles(root: Path, asset: str, closes: list[float], lows: list[float]) -> None:
    path = root / "data" / "canonical" / "kraken" / asset / "candles_1d.csv"
    candles = [
        Candle(
            timestamp=1_704_067_200 + index * 86_400,
            open=close - 1,
            high=close + 1,
            low=lows[index],
            close=close,
            volume=1_000 + index * 10,
            trade_count=100 + index,
            source="kraken_api",
        )
        for index, close in enumerate(closes)
    ]
    write_candles(path, candles)


def test_features_build_command(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    _write_daily_candles(
        tmp_path,
        "BTC",
        [100, 105, 110, 112, 115, 118, 120],
        [99, 104, 109, 111, 114, 117, 119],
    )
    _write_daily_candles(
        tmp_path,
        "ETH",
        [50, 52, 54, 53, 55, 57, 56],
        [49, 51, 53, 52, 48, 56, 55],
    )

    result = runner.invoke(
        app,
        [
            "features",
            "build",
            "--assets",
            "BTC",
            "--assets",
            "ETH",
            "--dataset-track",
            "dynamic_universe_kraken_only",
        ],
    )

    assert result.exit_code == 0
    assert '"dataset_id":' in result.stdout
    assert '"cached": false' in result.stdout
    assert '"dataset_track": "dynamic_universe_kraken_only"' in result.stdout
