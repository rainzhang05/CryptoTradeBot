"""Unit tests for shared simulated execution."""

from __future__ import annotations

import pytest

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


def test_apply_decision_uses_slippage_and_avoids_dust_positions() -> None:
    settings = BacktestSettings(
        initial_cash_usd=1_000.0,
        fee_rate_bps=0.0,
        slippage_bps=100.0,
        min_order_notional_usd=25.0,
        quantity_precision=4,
    )
    portfolio = PortfolioState(
        cash_usd=10.0,
        positions={"BTC": PositionState(asset="BTC", quantity=1.2, average_entry_price=90.0)},
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
        high=105.0,
        low=95.0,
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
    assert fills[0].fill_price == 99.0
    assert fills[0].quantity == 1.2
    assert "BTC" not in updated.positions


def test_apply_decision_tracks_entry_fees_in_cost_basis_and_realized_pnl() -> None:
    settings = BacktestSettings(
        initial_cash_usd=1_000.0,
        fee_rate_bps=100.0,
        slippage_bps=0.0,
        min_order_notional_usd=10.0,
        quantity_precision=8,
    )
    buy_portfolio = PortfolioState(cash_usd=1_000.0)
    buy_decision = DecisionSnapshot(
        timestamp=1_700_000_000,
        regime_state="constructive",
        exposure_fraction=1.0,
        target_weights={"BTC": 1.0},
        scores={"BTC": 1.0},
    )
    bar = Candle(
        timestamp=1_700_000_000,
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1_000.0,
        trade_count=100,
        source="kraken_api",
    )

    after_buy, _, buy_fills, _, _ = apply_decision(
        portfolio=buy_portfolio,
        decision=buy_decision,
        execution_bars={"BTC": bar},
        mark_bars={"BTC": bar},
        settings=settings,
    )

    assert len(buy_fills) == 1
    assert after_buy.positions["BTC"].average_entry_price == pytest.approx(101.0)

    sell_decision = DecisionSnapshot(
        timestamp=1_700_086_400,
        regime_state="defensive",
        exposure_fraction=0.0,
        target_weights={},
        scores={},
    )
    after_sell, _, sell_fills, _, _ = apply_decision(
        portfolio=after_buy,
        decision=sell_decision,
        execution_bars={"BTC": bar},
        mark_bars={"BTC": bar},
        settings=settings,
    )

    assert len(sell_fills) == 1
    assert "BTC" not in after_sell.positions
    assert after_sell.realized_pnl_usd == pytest.approx(after_sell.cash_usd - 1_000.0)
    assert sell_fills[0].realized_pnl_usd == pytest.approx(after_sell.realized_pnl_usd)
