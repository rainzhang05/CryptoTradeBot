"""Shared simulated execution path for backtest and simulate mode."""

from __future__ import annotations

from dataclasses import replace
from math import floor
from typing import Literal

from tradebot.backtest.models import (
    DecisionSnapshot,
    FillEvent,
    OrderIntent,
    PortfolioState,
    PositionState,
)
from tradebot.config import BacktestSettings
from tradebot.data.models import Candle


def apply_decision(
    *,
    portfolio: PortfolioState,
    decision: DecisionSnapshot,
    execution_bars: dict[str, Candle],
    mark_bars: dict[str, Candle],
    settings: BacktestSettings,
) -> tuple[PortfolioState, list[OrderIntent], list[FillEvent], float, float]:
    """Apply a target-weight decision through the simulated execution path."""
    fee_rate = settings.fee_rate_bps / 10_000
    slippage_rate = settings.slippage_bps / 10_000
    portfolio = replace(
        portfolio,
        last_timestamp=decision.timestamp,
        last_regime=decision.regime_state,
        last_risk_state=decision.risk_state,
        freeze_reason=decision.freeze_reason,
    )
    reference_prices = {asset: bar.open for asset, bar in execution_bars.items()}
    start_equity = _portfolio_equity(portfolio, reference_prices)
    peak_equity = max(
        portfolio.peak_equity_usd or settings.initial_cash_usd,
        start_equity,
        settings.initial_cash_usd,
    )

    if decision.is_frozen:
        end_equity = _portfolio_equity(
            portfolio,
            {asset: bar.close for asset, bar in mark_bars.items()},
        )
        gross_exposure = sum(
            position.quantity * mark_bars[asset].close
            for asset, position in portfolio.positions.items()
            if asset in mark_bars
        )
        frozen_portfolio = replace(
            portfolio,
            peak_equity_usd=max(peak_equity, end_equity),
        )
        return frozen_portfolio, [], [], end_equity, gross_exposure

    intents = build_order_intents(
        portfolio=portfolio,
        decision=decision,
        reference_prices=reference_prices,
        settings=settings,
        equity_usd=start_equity,
    )

    fills: list[FillEvent] = []
    for intent in intents:
        bar = execution_bars[intent.asset]
        if intent.side == "sell":
            portfolio, fill = _apply_sell(portfolio, intent, bar, fee_rate, slippage_rate, settings)
        else:
            portfolio, fill = _apply_buy(portfolio, intent, bar, fee_rate, slippage_rate, settings)
        if fill is not None:
            fills.append(fill)

    end_equity = _portfolio_equity(
        portfolio,
        {asset: bar.close for asset, bar in mark_bars.items()},
    )
    gross_exposure = sum(
        position.quantity * mark_bars[asset].close
        for asset, position in portfolio.positions.items()
        if asset in mark_bars
    )
    portfolio = replace(portfolio, peak_equity_usd=max(peak_equity, end_equity))
    return portfolio, intents, fills, end_equity, gross_exposure


def build_order_intents(
    *,
    portfolio: PortfolioState,
    decision: DecisionSnapshot,
    reference_prices: dict[str, float],
    settings: BacktestSettings,
    equity_usd: float,
) -> list[OrderIntent]:
    intents: list[OrderIntent] = []
    all_assets = set(portfolio.positions) | set(decision.target_weights)
    for asset in sorted(all_assets):
        price = reference_prices.get(asset)
        if price is None or price <= 0:
            continue
        target_weight = decision.target_weights.get(asset, 0.0)
        current_quantity = portfolio.positions.get(asset, PositionState(asset, 0.0, 0.0)).quantity
        current_notional = current_quantity * price
        current_weight = 0.0 if equity_usd <= 0 else current_notional / equity_usd
        if abs(target_weight - current_weight) < settings.rebalance_threshold:
            continue

        target_notional = equity_usd * target_weight
        delta_notional = target_notional - current_notional
        if abs(delta_notional) < settings.min_order_notional_usd:
            continue

        side: Literal["buy", "sell"] = "buy" if delta_notional > 0 else "sell"
        quantity = _round_quantity(abs(delta_notional) / price, settings.quantity_precision)
        if quantity <= 0:
            continue
        intents.append(
            OrderIntent(
                timestamp=decision.timestamp,
                asset=asset,
                side=side,
                target_weight=target_weight,
                reference_price=price,
                quantity=quantity,
            )
        )

    sells = [intent for intent in intents if intent.side == "sell"]
    buys = [intent for intent in intents if intent.side == "buy"]
    return sells + buys


def _apply_sell(
    portfolio: PortfolioState,
    intent: OrderIntent,
    bar: Candle,
    fee_rate: float,
    slippage_rate: float,
    settings: BacktestSettings,
) -> tuple[PortfolioState, FillEvent | None]:
    position = portfolio.positions.get(intent.asset)
    if position is None or position.quantity <= 0:
        return portfolio, None

    quantity = min(position.quantity, intent.quantity)
    fill_price = max(bar.low, bar.open * (1 - slippage_rate))

    remaining_quantity = position.quantity - quantity
    remaining_notional = remaining_quantity * fill_price
    if 0 < remaining_notional < settings.min_order_notional_usd:
        quantity = position.quantity
        remaining_quantity = 0.0

    gross_notional = quantity * fill_price
    if gross_notional < settings.min_order_notional_usd:
        return portfolio, None

    fee_paid = gross_notional * fee_rate
    realized_pnl = (fill_price - position.average_entry_price) * quantity - fee_paid

    new_positions = dict(portfolio.positions)
    if remaining_quantity <= 0:
        new_positions.pop(intent.asset, None)
    else:
        new_positions[intent.asset] = PositionState(
            asset=intent.asset,
            quantity=remaining_quantity,
            average_entry_price=position.average_entry_price,
        )

    updated_portfolio = PortfolioState(
        cash_usd=portfolio.cash_usd + gross_notional - fee_paid,
        positions=new_positions,
        realized_pnl_usd=portfolio.realized_pnl_usd + realized_pnl,
        fees_paid_usd=portfolio.fees_paid_usd + fee_paid,
        peak_equity_usd=portfolio.peak_equity_usd,
        last_timestamp=portfolio.last_timestamp,
        last_regime=portfolio.last_regime,
        last_risk_state=portfolio.last_risk_state,
        freeze_reason=portfolio.freeze_reason,
    )
    fill = FillEvent(
        timestamp=intent.timestamp,
        asset=intent.asset,
        side="sell",
        quantity=quantity,
        fill_price=fill_price,
        gross_notional_usd=gross_notional,
        fee_paid_usd=fee_paid,
        realized_pnl_usd=realized_pnl,
    )
    return updated_portfolio, fill


def _apply_buy(
    portfolio: PortfolioState,
    intent: OrderIntent,
    bar: Candle,
    fee_rate: float,
    slippage_rate: float,
    settings: BacktestSettings,
) -> tuple[PortfolioState, FillEvent | None]:
    fill_price = min(bar.high, bar.open * (1 + slippage_rate))
    affordable_quantity = portfolio.cash_usd / (fill_price * (1 + fee_rate))
    quantity = _round_quantity(
        min(intent.quantity, affordable_quantity),
        settings.quantity_precision,
    )
    if quantity <= 0:
        return portfolio, None

    gross_notional = quantity * fill_price
    if gross_notional < settings.min_order_notional_usd:
        return portfolio, None
    fee_paid = gross_notional * fee_rate
    total_cost = gross_notional + fee_paid
    if total_cost > portfolio.cash_usd:
        return portfolio, None

    position = portfolio.positions.get(intent.asset)
    if position is None:
        new_position = PositionState(
            asset=intent.asset,
            quantity=quantity,
            average_entry_price=total_cost / quantity,
        )
    else:
        new_quantity = position.quantity + quantity
        average_entry_price = (
            position.quantity * position.average_entry_price + total_cost
        ) / new_quantity
        new_position = PositionState(
            asset=intent.asset,
            quantity=new_quantity,
            average_entry_price=average_entry_price,
        )

    new_positions = dict(portfolio.positions)
    new_positions[intent.asset] = new_position
    updated_portfolio = PortfolioState(
        cash_usd=portfolio.cash_usd - total_cost,
        positions=new_positions,
        realized_pnl_usd=portfolio.realized_pnl_usd,
        fees_paid_usd=portfolio.fees_paid_usd + fee_paid,
        peak_equity_usd=portfolio.peak_equity_usd,
        last_timestamp=portfolio.last_timestamp,
        last_regime=portfolio.last_regime,
        last_risk_state=portfolio.last_risk_state,
        freeze_reason=portfolio.freeze_reason,
    )
    fill = FillEvent(
        timestamp=intent.timestamp,
        asset=intent.asset,
        side="buy",
        quantity=quantity,
        fill_price=fill_price,
        gross_notional_usd=gross_notional,
        fee_paid_usd=fee_paid,
        realized_pnl_usd=0.0,
    )
    return updated_portfolio, fill


def _portfolio_equity(portfolio: PortfolioState, prices: dict[str, float]) -> float:
    return portfolio.cash_usd + sum(
        position.quantity * prices.get(asset, 0.0)
        for asset, position in portfolio.positions.items()
    )


def _round_quantity(quantity: float, precision: int) -> float:
    factor = 10**precision
    rounded = floor(quantity * factor) / factor
    return float(rounded)
