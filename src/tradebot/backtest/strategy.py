"""Deterministic allocation policy for backtest and simulate mode."""

from __future__ import annotations

from tradebot.backtest.models import PortfolioState
from tradebot.config import AppConfig
from tradebot.strategy.service import StrategyEngine


def build_target_weights(
    *,
    timestamp: int,
    rows_by_asset: dict[str, dict[str, object]],
    config: AppConfig,
) -> tuple[str, float, dict[str, float], dict[str, float]]:
    """Build target portfolio weights from point-in-time feature rows."""
    engine = StrategyEngine(config)
    decision = engine.evaluate(
        timestamp=timestamp,
        rows_by_asset=rows_by_asset,
        portfolio=PortfolioState(
            cash_usd=config.backtest.initial_cash_usd,
            peak_equity_usd=config.backtest.initial_cash_usd,
        ),
        prices_by_asset={asset: 1.0 for asset in rows_by_asset},
    )
    return (
        decision.regime_state,
        decision.exposure_fraction,
        decision.target_weights,
        decision.scores,
    )