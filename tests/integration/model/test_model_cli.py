"""Integration tests for model CLI commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tradebot.cli import app
from tradebot.data.models import Candle
from tradebot.data.storage import write_candles
from tradebot.model.service import ModelService

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_promotion_backtest_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ModelService,
        "_promotion_backtest_comparison",
        lambda self, *, model_id, assets: {
            "hybrid": SimpleNamespace(run_id="hybrid-run", total_return=0.02),
            "rule_only": SimpleNamespace(run_id="rule-only-run", total_return=0.01),
            "incremental_total_return": 0.01,
        },
    )


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
model:
  initial_train_timestamps: 2
  minimum_validation_rows: 1
  minimum_walk_forward_splits: 1
  promotion_min_expected_return_correlation: -1.0
  promotion_max_downside_brier: 1.0
  promotion_max_sell_brier: 1.0
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


def test_model_train_validate_and_promote_commands(tmp_path: Path, monkeypatch) -> None:
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

    train_result = runner.invoke(app, ["model", "train", "--assets", "BTC", "--assets", "ETH"])
    assert train_result.exit_code == 0
    assert '"model_id":' in train_result.stdout

    validate_result = runner.invoke(app, ["model", "validate"])
    assert validate_result.exit_code == 0
    assert '"promotion_eligible": true' in validate_result.stdout.lower()

    promote_result = runner.invoke(app, ["model", "promote"])
    assert promote_result.exit_code == 0
    assert '"pointer_file":' in promote_result.stdout


def test_model_train_reports_actionable_error_when_history_is_too_short(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "initial_train_timestamps: 2",
            "initial_train_timestamps: 20",
        ),
        encoding="utf-8",
    )
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

    result = runner.invoke(app, ["model", "train", "--assets", "BTC", "--assets", "ETH"])

    assert result.exit_code == 1
    assert "Not enough usable aligned feature timestamps" in result.stderr


def test_model_promote_fails_cleanly_when_validation_gates_fail(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "promotion_min_expected_return_correlation: -1.0",
            "promotion_min_expected_return_correlation: 1.0",
        ),
        encoding="utf-8",
    )
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

    train_result = runner.invoke(app, ["model", "train", "--assets", "BTC", "--assets", "ETH"])
    assert train_result.exit_code == 0

    promote_result = runner.invoke(app, ["model", "promote"])

    assert promote_result.exit_code == 1
    assert "does not satisfy promotion rules" in promote_result.stderr


def test_model_promote_fails_cleanly_when_hybrid_matches_rule_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(
        ModelService,
        "_promotion_backtest_comparison",
        lambda self, *, model_id, assets: {
            "hybrid": SimpleNamespace(run_id="hybrid-run", total_return=0.02),
            "rule_only": SimpleNamespace(run_id="rule-only-run", total_return=0.02),
            "incremental_total_return": 0.0,
        },
    )

    train_result = runner.invoke(app, ["model", "train", "--assets", "BTC", "--assets", "ETH"])
    assert train_result.exit_code == 0

    promote_result = runner.invoke(app, ["model", "promote"])

    assert promote_result.exit_code == 1
    assert "does not improve on the rule-only baseline" in promote_result.stderr
