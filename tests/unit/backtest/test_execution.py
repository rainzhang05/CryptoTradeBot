"""Unit tests for shared simulated execution."""

from __future__ import annotations

from tradebot.backtest.execution import apply_decision
from tradebot.backtest.models import DecisionSnapshot, PortfolioState, PositionState
from tradebot.config import BacktestSettings
from tradebot.data.models import Candle


def test_apply_decision_buys_and_updates_portfolio() -> None:
    settings = BacktestSettings(initial_cash_usd=1_000.0, fee_rate_bps=0.0, slippage_bps=0.0)
    portfolio = PortfolioState(cash_usd=1_000.0)
    decision = DecisionSnapshot(
        timestamp=1_700_000_000,
        regime_state="constructive",
        exposure_fraction=0.5,
        target_weights={"BTC": 0.5},
        scores={"BTC": 1.0},
    )
    bar = Candle(
        timestamp=1_700_000_000,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000.0,
        trade_count=100,
        source="kraken_api",
    )

    updated, intents, fills, end_equity, _ = apply_decision(
        portfolio=portfolio,
        decision=decision,
        execution_bars={"BTC": bar},
        mark_bars={"BTC": bar},
        settings=settings,
    )

    assert len(intents) == 1
    assert len(fills) == 1
    assert fills[0].side == "buy"
    assert "BTC" in updated.positions
    assert updated.cash_usd < 1_000.0
    assert end_equity > 0


def test_apply_decision_sells_existing_position() -> None:
    settings = BacktestSettings(initial_cash_usd=1_000.0, fee_rate_bps=0.0, slippage_bps=0.0)
    portfolio = PortfolioState(
        cash_usd=100.0,
        positions={"BTC": PositionState(asset="BTC", quantity=2.0, average_entry_price=90.0)},
    )
    decision = DecisionSnapshot(
        timestamp=1_700_000_000,
        regime_state="defensive",
        exposure_fraction=0.0,
        target_weights={},
        scores={},
    )
    bar = Candle(
        timestamp=1_700_000_000,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000.0,
        trade_count=100,
        source="kraken_api",
    )

    updated, _, fills, _, _ = apply_decision(
        portfolio=portfolio,
        decision=decision,
        execution_bars={"BTC": bar},
        mark_bars={"BTC": bar},
        settings=settings,
    )

    assert len(fills) == 1
    assert fills[0].side == "sell"
    assert updated.cash_usd > 100.0
    assert "BTC" not in updated.positions