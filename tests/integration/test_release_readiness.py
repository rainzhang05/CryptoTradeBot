"""Integration test for the final release-readiness workflow."""

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
  environment: test
  log_format: console
runtime:
  default_mode: simulate
  max_cycles: 1
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


def _write_daily_series(root: Path, asset: str, closes: list[float], lows: list[float]) -> None:
    path = root / "data" / "canonical" / "kraken" / asset / "candles_1d.csv"
    candles = [
        Candle(
            timestamp=1_704_067_200 + index * 86_400,
            open=close - 0.5,
            high=close + 1.0,
            low=lows[index],
            close=close,
            volume=1_000 + index * 10,
            trade_count=100 + index,
            source="kraken_api",
        )
        for index, close in enumerate(closes)
    ]
    write_candles(path, candles)


def test_release_readiness_workflow(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    _write_daily_series(
        tmp_path,
        "BTC",
        [100, 101, 103, 106, 108, 111, 114, 118, 121, 125, 128, 132],
        [99, 100, 102, 105, 107, 110, 113, 117, 120, 124, 127, 131],
    )
    _write_daily_series(
        tmp_path,
        "ETH",
        [50, 51, 52, 53, 55, 58, 60, 63, 65, 68, 70, 73],
        [49, 50, 51, 52, 54, 57, 59, 62, 64, 67, 69, 72],
    )

    commands = [
        ["config", "validate"],
        ["data", "check", "--assets", "BTC", "--assets", "ETH"],
        ["data", "source"],
        ["features", "build", "--assets", "BTC", "--assets", "ETH"],
        ["backtest", "run", "--assets", "BTC", "--assets", "ETH"],
        ["backtest", "report"],
        ["run", "--mode", "simulate", "--max-cycles", "1"],
        ["status"],
        ["report", "list"],
        [
            "report",
            "export",
            "artifacts/reports/backtests/latest_backtest_report.json",
            str(tmp_path / "exports" / "latest_backtest_report.json"),
        ],
        ["email", "set", "trader@example.com"],
        ["logs", "tail", "--lines", "5"],
    ]

    outputs: list[str] = []
    for command in commands:
        result = runner.invoke(app, command)
        failure_message = "\n".join(
            [
                f"command failed: {' '.join(command)}",
                result.stdout,
                result.stderr,
            ]
        )
        assert result.exit_code == 0, failure_message
        outputs.append(result.stdout)

    assert "Configuration valid" in outputs[0]
    assert '"interval": "1d"' in outputs[1]
    assert '"asset": "ETH"' in outputs[2]
    assert '"dataset_id":' in outputs[3]
    assert '"run_id":' in outputs[4]
    assert '"final_equity_usd":' in outputs[5]
    assert "Completed 1 cycle(s) in simulate mode." in outputs[6]
    assert '"runtime_context":' in outputs[7]
    assert "artifacts/reports/backtests/latest_backtest_report.json" in outputs[8]
    assert (tmp_path / "exports" / "latest_backtest_report.json").exists()
    assert "trader@example.com" in config_path.read_text(encoding="utf-8")
    assert "runtime started" in outputs[11] or "runtime cycle completed" in outputs[11]
