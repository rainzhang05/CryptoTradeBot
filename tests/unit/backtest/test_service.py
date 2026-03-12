"""Unit tests for backtest and simulation orchestration."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tradebot.backtest.models import EquityPoint, PortfolioState, PositionState
from tradebot.backtest.service import BacktestService
from tradebot.config import load_config
from tradebot.data.models import Candle
from tradebot.data.storage import write_candles


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


def test_run_backtest_writes_report_and_artifacts(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
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

    summary = BacktestService(config).run_backtest(assets=("BTC", "ETH"))

    assert summary.fill_count >= 1
    assert summary.strategy_preset == "custom"
    assert Path(summary.report_file).exists()
    assert Path(summary.equity_curve_file).exists()
    assert Path(summary.decisions_file).exists()
    report_payload = Path(summary.report_file).read_text(encoding="utf-8")
    assert '"diagnostics"' in report_payload
    assert '"dataset_track"' in report_payload
    assert '"liquidation"' in report_payload
    assert '"strategy_preset"' in report_payload
    with Path(summary.equity_curve_file).open("r", encoding="utf-8", newline="") as handle:
        first_row = next(csv.DictReader(handle))
    assert float(first_row["equity_usd"]) == pytest.approx(1_000.0)
    assert float(first_row["gross_exposure"]) == pytest.approx(0.0)


def test_simulate_latest_cycle_persists_state_when_data_exists(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
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

    summary = BacktestService(config).simulate_latest_cycle(assets=("BTC", "ETH"))

    assert summary.status == "ok"
    assert summary.risk_state is not None
    assert Path(summary.state_file).exists()
    assert summary.dataset_id is not None


def test_dynamic_equal_weight_benchmark_adds_assets_when_they_list_later(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    service = BacktestService(config)
    start = 1_704_067_200
    day = 86_400
    bars_by_asset = {
        "BTC": {
            start: Candle(start, 100, 101, 99, 100, 1000, 100, "kraken_api"),
            start + day: Candle(start + day, 110, 111, 109, 110, 1000, 100, "kraken_api"),
            start + 2 * day: Candle(
                start + 2 * day, 121, 122, 120, 121, 1000, 100, "kraken_api"
            ),
        },
        "ETH": {
            start + day: Candle(start + day, 50, 51, 49, 50, 1000, 100, "kraken_api"),
            start + 2 * day: Candle(
                start + 2 * day, 100, 101, 99, 100, 1000, 100, "kraken_api"
            ),
        },
    }

    total_return = service._dynamic_equal_weight_total_return(
        bars_by_asset=bars_by_asset,
        start_timestamp=start,
        end_timestamp=start + 2 * day,
    )

    assert total_return == pytest.approx(0.705)


def test_yearly_returns_use_previous_year_end_equity_as_the_base(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    service = BacktestService(config)
    points = [
        EquityPoint(
            timestamp=1_735_603_200,
            equity_usd=1_000.0,
            cash_usd=1_000.0,
            gross_exposure=0.0,
        ),
        EquityPoint(
            timestamp=1_735_689_600,
            equity_usd=1_100.0,
            cash_usd=200.0,
            gross_exposure=900.0,
        ),
        EquityPoint(
            timestamp=1_767_139_200,
            equity_usd=1_210.0,
            cash_usd=300.0,
            gross_exposure=910.0,
        ),
    ]

    yearly = service._yearly_returns(points, decisions=[], fills=[])

    assert yearly["2024"]["total_return"] == pytest.approx(0.0)
    assert yearly["2025"]["start_equity_usd"] == pytest.approx(1_000.0)
    assert yearly["2025"]["total_return"] == pytest.approx(0.21)


def test_liquidation_metrics_estimate_terminal_exit_costs(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    object.__setattr__(config.backtest, "fee_rate_bps", 100.0)
    object.__setattr__(config.backtest, "slippage_bps", 100.0)
    service = BacktestService(config)
    portfolio = PortfolioState(
        cash_usd=200.0,
        positions={"BTC": PositionState(asset="BTC", quantity=2.0, average_entry_price=90.0)},
    )
    timestamp = 1_704_067_200
    mark_bars = {
        "BTC": Candle(
            timestamp=timestamp,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1_000.0,
            trade_count=100,
            source="kraken_api",
        )
    }

    liquidation = service._liquidation_metrics(portfolio=portfolio, mark_bars=mark_bars)

    assert liquidation["estimated_liquidation_slippage_usd"] == pytest.approx(2.0)
    assert liquidation["estimated_liquidation_fee_usd"] == pytest.approx(1.98)
    assert liquidation["net_liquidation_equity_usd"] == pytest.approx(396.02)
    assert liquidation["net_liquidation_total_return"] == pytest.approx(-0.60398)
