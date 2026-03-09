"""Integration tests for research sweep CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tradebot.cli import app
from tradebot.data.models import Candle
from tradebot.data.storage import write_candles

runner = CliRunner()

ALL_ASSETS = ("BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "DOGE", "TRX", "AVAX", "LINK")


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app:
  log_level: ERROR
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
model:
  initial_train_timestamps: 2
  minimum_validation_rows: 1
  minimum_walk_forward_splits: 1
  promotion_min_expected_return_correlation: -1.0
  promotion_max_downside_brier: 1.0
  promotion_max_sell_brier: 1.0
backtest:
  initial_cash_usd: 1000.0
  fee_rate_bps: 0.0
  slippage_bps: 0.0
  max_positions: 3
  max_asset_weight: 0.35
  min_order_notional_usd: 10.0
  rebalance_threshold: 0.01
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    return config_path


def _write_daily_series(root: Path, asset: str, closes: list[float]) -> None:
    path = root / "data" / "canonical" / "kraken" / asset / "candles_1d.csv"
    candles = [
        Candle(
            timestamp=1_704_067_200 + index * 86_400,
            open=close - 0.5,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=1_000.0 + index * 20,
            trade_count=100 + index,
            source="kraken_api",
        )
        for index, close in enumerate(closes)
    ]
    write_candles(path, candles)


def _seed_all_assets(root: Path) -> None:
    base = [100, 101, 103, 106, 108, 111, 114, 118, 121, 125, 128, 132]
    for offset, asset in enumerate(ALL_ASSETS):
        closes = [value + (offset * 0.5) for value in base]
        _write_daily_series(root, asset, closes)


def test_research_sweep_resume_and_report_commands(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    _seed_all_assets(tmp_path)

    first = runner.invoke(
        app,
        ["research", "sweep", "--preset", "broad_staged", "--limit", "1"],
    )
    assert first.exit_code == 0
    first_payload = json.loads(first.stdout)
    assert first_payload["completed_experiments"] == 1
    assert first_payload["limit_reached"] is True

    second = runner.invoke(
        app,
        [
            "research",
            "sweep",
            "--preset",
            "broad_staged",
            "--resume",
            "--limit",
            "2",
        ],
    )
    assert second.exit_code == 0
    second_payload = json.loads(second.stdout)
    assert second_payload["sweep_id"] == first_payload["sweep_id"]
    assert second_payload["completed_experiments"] == 2
    assert second_payload["executed_experiments"] == 1

    report = runner.invoke(app, ["research", "report"])
    assert report.exit_code == 0
    report_payload = json.loads(report.stdout)
    assert report_payload["sweep_id"] == first_payload["sweep_id"]
    assert report_payload["leaderboard"]["rule_only"]
    assert report_payload["leaderboard"]["hybrid"]

    results_path = Path(second_payload["results_file"])
    with results_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(handle.read().splitlines())
    assert len(rows) == 3

    comparison_dir = Path(report_payload["comparison_dir"])
    comparison_files = sorted(comparison_dir.glob("*.json"))
    assert comparison_files
    comparison_payload = json.loads(comparison_files[0].read_text(encoding="utf-8"))
    assert "benchmarks_json" in comparison_payload["candidate"]
    assert "yearly_returns_json" in comparison_payload["candidate"]
