"""Typed models for live Kraken execution and persisted runtime state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from tradebot.backtest.models import FillEvent, PositionState

OrderSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class PairMetadata:
    """Market metadata needed to submit spot orders safely."""

    pair: str
    altname: str
    wsname: str | None
    status: str | None
    lot_decimals: int
    ordermin: float | None
    costmin: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KrakenOrderState:
    """Normalized Kraken order state from open or queried orders."""

    txid: str
    pair: str
    side: OrderSide
    order_type: str
    status: str
    requested_volume: float
    executed_volume: float
    remaining_volume: float
    average_price: float | None
    cost_usd: float | None
    fee_usd: float | None
    opened_at: float | None
    closed_at: float | None
    limit_price: float | None = None
    userref: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderSubmission:
    """Response details for a newly submitted Kraken order."""

    txid: str
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveState:
    """Persisted live runtime state used for restart-safe reconciliation."""

    cash_usd: float
    positions: dict[str, PositionState] = field(default_factory=dict)
    open_orders: dict[str, KrakenOrderState] = field(default_factory=dict)
    recent_fills: list[FillEvent] = field(default_factory=list)
    last_decision_timestamp: int | None = None
    last_regime: str | None = None
    last_risk_state: str | None = None
    peak_equity_usd: float | None = None
    consecutive_order_failures: int = 0
    freeze_reason: str | None = None
    incidents: list[str] = field(default_factory=list)
    last_synced_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash_usd": self.cash_usd,
            "positions": {asset: position.to_dict() for asset, position in self.positions.items()},
            "open_orders": {txid: order.to_dict() for txid, order in self.open_orders.items()},
            "recent_fills": [fill.to_dict() for fill in self.recent_fills],
            "last_decision_timestamp": self.last_decision_timestamp,
            "last_regime": self.last_regime,
            "last_risk_state": self.last_risk_state,
            "peak_equity_usd": self.peak_equity_usd,
            "consecutive_order_failures": self.consecutive_order_failures,
            "freeze_reason": self.freeze_reason,
            "incidents": list(self.incidents),
            "last_synced_at": self.last_synced_at,
        }


@dataclass(frozen=True)
class LiveCycleSummary:
    """Summary of one live runtime cycle."""

    dataset_id: str | None
    timestamp: int | None
    status: str
    system_status: str
    connectivity_state: str
    regime_state: str | None
    risk_state: str | None
    equity_usd: float
    cash_usd: float
    fill_count: int
    fills: list[FillEvent]
    holdings: dict[str, float]
    open_order_count: int
    incidents: list[str]
    state_file: str
    freeze_reason: str | None = None
    decision_executed: bool = False
    portfolio_drawdown: float | None = None
    target_weights: dict[str, float] = field(default_factory=dict)
    decision_actions: dict[str, str] = field(default_factory=dict)
    decision_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "system_status": self.system_status,
            "connectivity_state": self.connectivity_state,
            "regime_state": self.regime_state,
            "risk_state": self.risk_state,
            "equity_usd": self.equity_usd,
            "cash_usd": self.cash_usd,
            "fill_count": self.fill_count,
            "fills": [fill.to_dict() for fill in self.fills],
            "holdings": self.holdings,
            "open_order_count": self.open_order_count,
            "incidents": list(self.incidents),
            "state_file": self.state_file,
            "freeze_reason": self.freeze_reason,
            "decision_executed": self.decision_executed,
            "portfolio_drawdown": self.portfolio_drawdown,
            "target_weights": self.target_weights,
            "decision_actions": self.decision_actions,
            "decision_reasons": self.decision_reasons,
        }
