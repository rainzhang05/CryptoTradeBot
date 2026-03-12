"""Shared models for backtest and simulation workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

OrderSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class PositionState:
    asset: str
    quantity: float
    average_entry_price: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioState:
    cash_usd: float
    positions: dict[str, PositionState] = field(default_factory=dict)
    realized_pnl_usd: float = 0.0
    fees_paid_usd: float = 0.0
    peak_equity_usd: float | None = None
    last_timestamp: int | None = None
    last_regime: str | None = None
    last_risk_state: str | None = None
    freeze_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash_usd": self.cash_usd,
            "positions": {asset: position.to_dict() for asset, position in self.positions.items()},
            "realized_pnl_usd": self.realized_pnl_usd,
            "fees_paid_usd": self.fees_paid_usd,
            "peak_equity_usd": self.peak_equity_usd,
            "last_timestamp": self.last_timestamp,
            "last_regime": self.last_regime,
            "last_risk_state": self.last_risk_state,
            "freeze_reason": self.freeze_reason,
        }


@dataclass(frozen=True)
class OrderIntent:
    timestamp: int
    asset: str
    side: OrderSide
    target_weight: float
    reference_price: float
    quantity: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FillEvent:
    timestamp: int
    asset: str
    side: OrderSide
    quantity: float
    fill_price: float
    gross_notional_usd: float
    fee_paid_usd: float
    realized_pnl_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionSnapshot:
    timestamp: int
    regime_state: str
    exposure_fraction: float
    target_weights: dict[str, float]
    scores: dict[str, float]
    risk_state: str = "normal"
    is_frozen: bool = False
    freeze_reason: str | None = None
    asset_actions: dict[str, str] = field(default_factory=dict)
    asset_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EquityPoint:
    timestamp: int
    equity_usd: float
    cash_usd: float
    gross_exposure: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BacktestRunSummary:
    run_id: str
    report_file: str
    fills_file: str
    equity_curve_file: str
    decisions_file: str
    dataset_id: str
    strategy_preset: str
    decision_count: int
    fill_count: int
    final_equity_usd: float
    total_return: float
    max_drawdown: float
    total_fees_usd: float
    net_liquidation_equity_usd: float | None = None
    net_liquidation_total_return: float | None = None
    estimated_liquidation_fee_usd: float | None = None
    estimated_liquidation_slippage_usd: float | None = None
    start_timestamp: int | None = None
    end_timestamp: int | None = None
    cagr: float | None = None
    calmar_ratio: float | None = None
    annualized_volatility: float | None = None
    daily_sharpe: float | None = None
    turnover: float | None = None
    fee_to_gross_pnl_ratio: float | None = None
    days_invested: int | None = None
    trades_per_year: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimulationCycleSummary:
    dataset_id: str | None
    timestamp: int | None
    status: str
    regime_state: str | None
    risk_state: str | None
    equity_usd: float
    cash_usd: float
    fill_count: int
    fills: list[FillEvent]
    state_file: str
    freeze_reason: str | None = None
    holdings: dict[str, float] = field(default_factory=dict)
    incidents: list[str] = field(default_factory=list)
    portfolio_drawdown: float | None = None
    target_weights: dict[str, float] = field(default_factory=dict)
    decision_actions: dict[str, str] = field(default_factory=dict)
    decision_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "regime_state": self.regime_state,
            "risk_state": self.risk_state,
            "equity_usd": self.equity_usd,
            "cash_usd": self.cash_usd,
            "fill_count": self.fill_count,
            "fills": [fill.to_dict() for fill in self.fills],
            "state_file": self.state_file,
            "freeze_reason": self.freeze_reason,
            "holdings": self.holdings,
            "incidents": list(self.incidents),
            "portfolio_drawdown": self.portfolio_drawdown,
            "target_weights": self.target_weights,
            "decision_actions": self.decision_actions,
            "decision_reasons": self.decision_reasons,
        }
