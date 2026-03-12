"""Integration tests for backtest CLI commands."""

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
  momentum_windows_days: [2, 4]
  trend_windows_days: [2, 4]
  volatility_windows_days: [2]
  relative_strength_window_days: 2
  breadth_window_days: 2
  dollar_volume_window_days: 2
  source_window_days: 2
backtest:
  initial_cash_usd: 1000.0
  fee_rate_bps: 0.0
  slippage_bps: 0.0
  max_positions: 2
  max_asset_weight: 0.35
  min_order_notional_usd: 10.0
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


def test_backtest_run_and_report_commands(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    _write_daily_series(
        tmp_path,
        "BTC",
        [100, 101, 103, 106, 108, 111, 114, 118, 121, 125, 128, 132],
    )
    _write_daily_series(
        tmp_path,
        "ETH",
        [50, 51, 52, 53, 55, 58, 60, 63, 65, 68, 70, 73],
    )

    run_result = runner.invoke(
        app,
        [
            "backtest",
            "run",
            "--assets",
            "BTC",
            "--assets",
            "ETH",
            "--dataset-track",
            "dynamic_universe_kraken_only",
            "--strategy-preset",
            "max_profit",
        ],
    )
    report_result = runner.invoke(app, ["backtest", "report"])

    assert run_result.exit_code == 0
    assert '"run_id":' in run_result.stdout
    assert '"dataset_id":' in run_result.stdout
    assert '"strategy_preset": "max_profit"' in run_result.stdout
    assert report_result.exit_code == 0
    assert '"final_equity_usd":' in report_result.stdout
    assert '"net_liquidation_total_return":' in report_result.stdout
