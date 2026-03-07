"""Unit tests for backtest allocation policy."""

from __future__ import annotations

from pathlib import Path

from tradebot.backtest.strategy import build_target_weights
from tradebot.config import load_config


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
research:
  momentum_windows_days: [2, 4]
  trend_windows_days: [2, 4]
  volatility_windows_days: [2]
  relative_strength_window_days: 2
  breadth_window_days: 2
  dollar_volume_window_days: 2
  source_window_days: 2
backtest:
  max_positions: 2
  max_asset_weight: 0.35
  constructive_exposure: 1.0
  neutral_exposure: 0.5
  defensive_exposure: 0.25
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    return config_path


def test_build_target_weights_scales_to_regime_and_caps_weights(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    regime_state, exposure_fraction, target_weights, scores = build_target_weights(
        timestamp=1_700_000_000,
        rows_by_asset={
            "BTC": {
                "asset": "BTC",
                "regime_state": "constructive",
                "source_confidence_2d": 1.0,
                "liquidity_sanity_flag": 1.0,
                "momentum_2d": 0.08,
                "momentum_4d": 0.12,
                "relative_strength_2d": 0.05,
                "trend_gap_2d": 0.04,
                "trend_gap_4d": 0.06,
                "realized_volatility_2d": 0.02,
            },
            "ETH": {
                "asset": "ETH",
                "regime_state": "constructive",
                "source_confidence_2d": 1.0,
                "liquidity_sanity_flag": 1.0,
                "momentum_2d": 0.06,
                "momentum_4d": 0.10,
                "relative_strength_2d": 0.04,
                "trend_gap_2d": 0.03,
                "trend_gap_4d": 0.04,
                "realized_volatility_2d": 0.03,
            },
        },
        config=config,
    )

    assert regime_state == "constructive"
    assert exposure_fraction == 1.0
    assert set(target_weights) == {"BTC", "ETH"}
    assert all(weight <= 0.35 for weight in target_weights.values())
    assert sum(target_weights.values()) <= 0.7
    assert scores["BTC"] > scores["ETH"] > 0