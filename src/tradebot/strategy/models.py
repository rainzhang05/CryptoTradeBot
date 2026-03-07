"""Typed models for deterministic strategy decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

AssetAction = Literal["blocked", "enter", "hold", "increase", "reduce", "exit"]
RiskState = Literal[
    "normal",
    "elevated_caution",
    "reduced_aggressiveness",
    "catastrophe",
    "frozen",
]


@dataclass(frozen=True)
class AssetDecision:
    """Rule-engine output for a single asset at one decision point."""

    asset: str
    action: AssetAction
    reason: str
    score: float
    current_weight: float
    target_weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyDecision:
    """Complete deterministic decision for one portfolio evaluation."""

    timestamp: int
    regime_state: str
    risk_state: RiskState
    exposure_fraction: float
    target_weights: dict[str, float]
    scores: dict[str, float]
    asset_decisions: dict[str, AssetDecision]
    is_frozen: bool = False
    freeze_reason: str | None = None
    current_equity_usd: float | None = None
    peak_equity_usd: float | None = None
    portfolio_drawdown: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "regime_state": self.regime_state,
            "risk_state": self.risk_state,
            "exposure_fraction": self.exposure_fraction,
            "target_weights": self.target_weights,
            "scores": self.scores,
            "asset_decisions": {
                asset: decision.to_dict() for asset, decision in self.asset_decisions.items()
            },
            "is_frozen": self.is_frozen,
            "freeze_reason": self.freeze_reason,
            "current_equity_usd": self.current_equity_usd,
            "peak_equity_usd": self.peak_equity_usd,
            "portfolio_drawdown": self.portfolio_drawdown,
        }