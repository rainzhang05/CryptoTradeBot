"""Unit tests for the Phase 5 deterministic rule engine."""

from __future__ import annotations

from pathlib import Path

from tradebot.backtest.models import PortfolioState, PositionState
from tradebot.config import load_config
from tradebot.strategy.models import ResearchStrategyProfile
from tradebot.strategy.service import StrategyEngine


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
  min_source_confidence: 0.8
  entry_momentum_floor: 0.0
  entry_trend_gap_floor: 0.0
  hold_momentum_floor: -0.03
  hold_trend_gap_floor: -0.03
  max_realized_volatility: 0.25
  reduction_volatility_threshold: 0.12
  severe_momentum_floor: -0.08
  severe_trend_gap_floor: -0.05
  weak_relative_strength_floor: -0.03
  reduction_target_fraction: 0.5
  held_asset_score_bonus: 0.02
  drawdown_caution_threshold: 0.10
  drawdown_reduced_threshold: 0.20
  drawdown_catastrophe_threshold: 0.30
  elevated_caution_exposure_multiplier: 0.8
  reduced_aggressiveness_exposure_multiplier: 0.6
  catastrophe_exposure_multiplier: 0.3
research:
  momentum_windows_days: [2, 4]
  trend_windows_days: [2, 4]
  volatility_windows_days: [2]
  relative_strength_window_days: 2
  breadth_window_days: 2
  dollar_volume_window_days: 2
  source_window_days: 2
backtest:
  initial_cash_usd: 1000.0
  max_positions: 2
  max_asset_weight: 0.35
  constructive_exposure: 1.0
  neutral_exposure: 0.5
  defensive_exposure: 0.25
  rebalance_threshold: 0.01
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    return config_path


def _row(*, asset: str, regime_state: str, source_confidence: float = 1.0, liquidity: float = 1.0,
         short_momentum: float = 0.05, long_momentum: float = 0.08, relative_strength: float = 0.04,
         short_trend: float = 0.03, long_trend: float = 0.05, volatility: float = 0.04,
         breadth_positive: float = 0.7, breadth_above_trend: float = 0.7) -> dict[str, object]:
    return {
        "asset": asset,
        "regime_state": regime_state,
        "source_confidence_2d": source_confidence,
        "liquidity_sanity_flag": liquidity,
        "momentum_2d": short_momentum,
        "momentum_4d": long_momentum,
        "relative_strength_2d": relative_strength,
        "trend_gap_2d": short_trend,
        "trend_gap_4d": long_trend,
        "realized_volatility_2d": volatility,
        "universe_breadth_positive_2d": breadth_positive,
        "universe_breadth_above_trend_2d": breadth_above_trend,
    }


def test_strategy_engine_reduces_held_assets_in_defensive_regime(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    engine = StrategyEngine(config)
    portfolio = PortfolioState(
        cash_usd=800.0,
        positions={
            "BTC": PositionState(asset="BTC", quantity=2.0, average_entry_price=95.0),
        },
        peak_equity_usd=1_000.0,
    )

    decision = engine.evaluate(
        timestamp=1_700_000_000,
        rows_by_asset={
            "BTC": _row(
                asset="BTC",
                regime_state="defensive",
                short_momentum=-0.01,
                long_momentum=0.01,
                relative_strength=-0.01,
                short_trend=-0.01,
                long_trend=0.0,
                volatility=0.08,
                breadth_positive=0.34,
                breadth_above_trend=0.45,
            ),
            "ETH": _row(asset="ETH", regime_state="defensive"),
        },
        portfolio=portfolio,
        prices_by_asset={"BTC": 100.0, "ETH": 50.0},
    )

    assert decision.is_frozen is False
    assert decision.risk_state == "reduced_aggressiveness"
    assert decision.asset_decisions["BTC"].action == "reduce"
    assert decision.asset_decisions["BTC"].target_weight == 0.1
    assert decision.asset_decisions["ETH"].action == "blocked"
    assert decision.exposure_fraction == 0.15
    assert decision.target_weights == {"BTC": 0.1}


def test_strategy_engine_freezes_when_held_asset_lacks_signal_row(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    engine = StrategyEngine(config)
    portfolio = PortfolioState(
        cash_usd=500.0,
        positions={
            "BTC": PositionState(asset="BTC", quantity=1.0, average_entry_price=100.0),
        },
        peak_equity_usd=1_000.0,
    )

    decision = engine.evaluate(
        timestamp=1_700_000_000,
        rows_by_asset={"ETH": _row(asset="ETH", regime_state="constructive")},
        portfolio=portfolio,
        prices_by_asset={"BTC": 100.0, "ETH": 50.0},
    )

    assert decision.is_frozen is True
    assert decision.risk_state == "frozen"
    assert decision.freeze_reason == "missing_signal:BTC"
    assert decision.asset_decisions["BTC"].action == "hold"
    assert decision.target_weights == {}


def test_strategy_engine_exits_on_low_source_confidence(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    engine = StrategyEngine(config)
    portfolio = PortfolioState(
        cash_usd=900.0,
        positions={
            "BTC": PositionState(asset="BTC", quantity=1.0, average_entry_price=100.0),
        },
        peak_equity_usd=1_000.0,
    )

    decision = engine.evaluate(
        timestamp=1_700_000_000,
        rows_by_asset={
            "BTC": _row(asset="BTC", regime_state="constructive", source_confidence=0.5),
        },
        portfolio=portfolio,
        prices_by_asset={"BTC": 100.0},
    )

    assert decision.is_frozen is False
    assert decision.asset_decisions["BTC"].action == "exit"
    assert decision.asset_decisions["BTC"].reason == "low_source_confidence"
    assert decision.target_weights == {}


def test_strategy_engine_blocks_entry_on_high_downside_prediction(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    engine = StrategyEngine(config)
    portfolio = PortfolioState(cash_usd=1_000.0, peak_equity_usd=1_000.0)

    decision = engine.evaluate(
        timestamp=1_700_000_000,
        rows_by_asset={
            "BTC": _row(
                asset="BTC",
                regime_state="constructive",
                breadth_positive=0.8,
                breadth_above_trend=0.8,
            )
            | {
                "expected_return_score": 0.08,
                "downside_risk_score": 0.9,
                "sell_risk_score": 0.1,
            }
        },
        portfolio=portfolio,
        prices_by_asset={"BTC": 100.0},
    )

    assert decision.asset_decisions["BTC"].action == "blocked"
    assert decision.asset_decisions["BTC"].reason == "entry_filter_failed"
    assert decision.target_weights == {}


def test_strategy_engine_ignores_disabled_downside_head_in_research_profile(
    tmp_path: Path,
) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    profile = ResearchStrategyProfile(
        expected_return_head_enabled=True,
        downside_risk_head_enabled=False,
        sell_risk_head_enabled=True,
    )
    engine = StrategyEngine(config, research_profile=profile)
    portfolio = PortfolioState(cash_usd=1_000.0, peak_equity_usd=1_000.0)

    decision = engine.evaluate(
        timestamp=1_700_000_000,
        rows_by_asset={
            "BTC": _row(
                asset="BTC",
                regime_state="constructive",
                breadth_positive=0.8,
                breadth_above_trend=0.8,
            )
            | {
                "expected_return_score": 0.08,
                "downside_risk_score": 0.95,
                "sell_risk_score": 0.1,
            }
        },
        portfolio=portfolio,
        prices_by_asset={"BTC": 100.0},
    )

    assert decision.asset_decisions["BTC"].action == "enter"
    assert decision.target_weights == {"BTC": 0.35}
