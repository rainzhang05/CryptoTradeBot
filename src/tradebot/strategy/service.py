"""Deterministic Phase 5 rule engine."""

from __future__ import annotations

from dataclasses import replace

from tradebot.backtest.models import PortfolioState
from tradebot.config import AppConfig
from tradebot.strategy.models import (
    AssetAction,
    AssetDecision,
    ResearchStrategyProfile,
    RiskState,
    StrategyDecision,
)


class StrategyEngine:
    """Apply deterministic rule-based portfolio construction and risk controls."""

    def __init__(
        self,
        config: AppConfig,
        research_profile: ResearchStrategyProfile | None = None,
    ) -> None:
        self.config = config
        self.strategy_settings = config.strategy
        self.research_settings = config.research
        self.backtest_settings = config.backtest
        self.research_profile = research_profile or self._default_research_profile()

    def _default_research_profile(self) -> ResearchStrategyProfile:
        return ResearchStrategyProfile(
            regime_layer_enabled=self.strategy_settings.regime_layer_enabled,
            entry_filter_layer_enabled=self.strategy_settings.entry_filter_layer_enabled,
            volatility_layer_enabled=self.strategy_settings.volatility_layer_enabled,
            gradual_reduction_layer_enabled=(
                self.strategy_settings.gradual_reduction_layer_enabled
            ),
        )

    def evaluate(
        self,
        *,
        timestamp: int,
        rows_by_asset: dict[str, dict[str, object]],
        portfolio: PortfolioState,
        prices_by_asset: dict[str, float],
    ) -> StrategyDecision:
        scoped_rows = {
            asset: row
            for asset, row in rows_by_asset.items()
            if asset in self.strategy_settings.fixed_universe
        }
        current_equity = self._portfolio_equity(portfolio, prices_by_asset)
        peak_equity = max(
            portfolio.peak_equity_usd or self.backtest_settings.initial_cash_usd,
            current_equity,
            self.backtest_settings.initial_cash_usd,
        )
        portfolio_drawdown = 0.0 if peak_equity <= 0 else (current_equity / peak_equity) - 1

        regime_state = self._regime_state(scoped_rows)
        freeze_reason = self._freeze_reason(
            regime_state=regime_state,
            rows_by_asset=scoped_rows,
            portfolio=portfolio,
            prices_by_asset=prices_by_asset,
        )
        risk_state = self._risk_state(
            regime_state=regime_state,
            portfolio_drawdown=portfolio_drawdown,
            freeze_reason=freeze_reason,
        )
        exposure_fraction = self._target_exposure(regime_state=regime_state, risk_state=risk_state)

        if freeze_reason is not None:
            return StrategyDecision(
                timestamp=timestamp,
                regime_state=regime_state,
                risk_state=risk_state,
                exposure_fraction=0.0,
                target_weights={},
                scores={},
                asset_decisions=self._frozen_asset_decisions(portfolio, scoped_rows),
                is_frozen=True,
                freeze_reason=freeze_reason,
                current_equity_usd=current_equity,
                peak_equity_usd=peak_equity,
                portfolio_drawdown=portfolio_drawdown,
            )

        current_weights = self._current_weights(portfolio, prices_by_asset, current_equity)
        preliminary: dict[str, AssetDecision] = {}
        scores: dict[str, float] = {}
        reserved_weights: dict[str, float] = {}

        asset_scope = tuple(sorted(set(scoped_rows) | set(portfolio.positions)))
        for asset in asset_scope:
            row = scoped_rows.get(asset)
            if row is None:
                if asset in portfolio.positions:
                    preliminary[asset] = AssetDecision(
                        asset=asset,
                        action="exit",
                        reason="unsupported_asset",
                        score=0.0,
                        current_weight=current_weights.get(asset, 0.0),
                        target_weight=0.0,
                    )
                continue

            decision = self._evaluate_asset(
                asset=asset,
                row=row,
                held=asset in portfolio.positions,
                current_weight=current_weights.get(asset, 0.0),
                regime_state=regime_state,
                exposure_fraction=exposure_fraction,
            )
            preliminary[asset] = decision
            if decision.action == "reduce" and decision.target_weight > 0:
                reserved_weights[asset] = decision.target_weight
            if decision.action in {"enter", "hold"} and decision.score > 0:
                scores[asset] = decision.score

        target_weights = self._build_target_weights(scores, reserved_weights, exposure_fraction)
        asset_decisions = self._finalize_asset_decisions(
            portfolio=portfolio,
            preliminary=preliminary,
            target_weights=target_weights,
            current_weights=current_weights,
        )
        return StrategyDecision(
            timestamp=timestamp,
            regime_state=regime_state,
            risk_state=risk_state,
            exposure_fraction=exposure_fraction,
            target_weights=target_weights,
            scores=scores,
            asset_decisions=asset_decisions,
            is_frozen=False,
            freeze_reason=None,
            current_equity_usd=current_equity,
            peak_equity_usd=peak_equity,
            portfolio_drawdown=portfolio_drawdown,
        )

    def _evaluate_asset(
        self,
        *,
        asset: str,
        row: dict[str, object],
        held: bool,
        current_weight: float,
        regime_state: str,
        exposure_fraction: float,
    ) -> AssetDecision:
        source_confidence = self._float_value(
            row,
            f"source_confidence_{self.research_settings.source_window_days}d",
        )
        liquidity_flag = self._float_value(row, "liquidity_sanity_flag")
        short_momentum = self._float_value(
            row,
            f"momentum_{self.research_settings.momentum_windows_days[0]}d",
        )
        long_momentum = self._float_value(
            row,
            f"momentum_{self.research_settings.momentum_windows_days[-1]}d",
        )
        short_trend = self._float_value(
            row,
            f"trend_gap_{self.research_settings.trend_windows_days[0]}d",
        )
        long_trend = self._float_value(
            row,
            f"trend_gap_{self.research_settings.trend_windows_days[-1]}d",
        )
        relative_strength = self._float_value(
            row,
            f"relative_strength_{self.research_settings.relative_strength_window_days}d",
        )
        volatility = self._float_value(
            row,
            f"realized_volatility_{self.research_settings.volatility_windows_days[0]}d",
        )
        breadth_positive = self._float_or_default(
            row,
            f"universe_breadth_positive_{self.research_settings.breadth_window_days}d",
            default=0.5,
        )
        breadth_above_trend = self._float_or_default(
            row,
            f"universe_breadth_above_trend_{self.research_settings.breadth_window_days}d",
            default=0.5,
        )

        if source_confidence < self.strategy_settings.min_source_confidence:
            return self._asset_decision(
                asset=asset,
                action="exit" if held else "blocked",
                reason="low_source_confidence",
                score=0.0,
                current_weight=current_weight,
            )
        if liquidity_flag < 1:
            return self._asset_decision(
                asset=asset,
                action="exit" if held else "blocked",
                reason="liquidity_invalid",
                score=0.0,
                current_weight=current_weight,
            )

        defensive_entry_allowed = (
            regime_state == "defensive"
            and long_momentum >= self.strategy_settings.hold_momentum_floor
            and long_trend >= self.strategy_settings.hold_trend_gap_floor
            and relative_strength > self.strategy_settings.weak_relative_strength_floor
            and volatility <= self.strategy_settings.max_realized_volatility
            and short_trend >= self.strategy_settings.entry_trend_gap_floor
        )
        entry_eligible = True
        if self.research_profile.regime_layer_enabled:
            entry_eligible = entry_eligible and (
                regime_state != "defensive" or defensive_entry_allowed
            )
        if self.research_profile.entry_filter_layer_enabled:
            entry_eligible = (
                entry_eligible
                and short_momentum >= self.strategy_settings.entry_momentum_floor
                and long_momentum >= self.strategy_settings.entry_momentum_floor
                and short_trend >= self.strategy_settings.entry_trend_gap_floor
                and long_trend >= self.strategy_settings.entry_trend_gap_floor
            )
        if self.research_profile.volatility_layer_enabled:
            entry_eligible = (
                entry_eligible
                and volatility <= self.strategy_settings.max_realized_volatility
            )
        hold_eligible = (
            long_momentum >= self.strategy_settings.hold_momentum_floor
            and long_trend >= self.strategy_settings.hold_trend_gap_floor
        )
        reduction_signal = (
            short_momentum < 0
            or relative_strength <= self.strategy_settings.weak_relative_strength_floor
            or breadth_above_trend < 0.5
        )
        if self.research_profile.volatility_layer_enabled:
            reduction_signal = (
                reduction_signal
                or volatility >= self.strategy_settings.reduction_volatility_threshold
            )

        rule_score = self._score_asset(
            short_momentum=short_momentum,
            long_momentum=long_momentum,
            short_trend=short_trend,
            long_trend=long_trend,
            relative_strength=relative_strength,
            volatility=(
                volatility if self.research_profile.volatility_layer_enabled else 0.0
            ),
            breadth_positive=breadth_positive,
            breadth_above_trend=breadth_above_trend,
            held=held,
        )
        score = rule_score

        if held and hold_eligible and regime_state in {"constructive", "neutral"}:
            score = max(score, self.strategy_settings.held_asset_score_bonus * 0.5)

        if (
            held
            and self.research_profile.gradual_reduction_layer_enabled
            and (
                (
                    self.research_profile.regime_layer_enabled
                    and regime_state == "defensive"
                )
                or not hold_eligible
                or reduction_signal
            )
        ):
            reduction_floor = min(
                self.backtest_settings.max_asset_weight,
                exposure_fraction / self.backtest_settings.max_positions,
            )
            reduced_weight = min(
                current_weight,
                max(
                    current_weight * self.strategy_settings.reduction_target_fraction,
                    reduction_floor,
                ),
            )
            if reduced_weight >= current_weight - self.backtest_settings.rebalance_threshold:
                return self._asset_decision(
                    asset=asset,
                    action="hold",
                    reason="held_supportive",
                    score=max(score, 0.0),
                    current_weight=current_weight,
                    target_weight=current_weight,
                )
            return self._asset_decision(
                asset=asset,
                action="reduce",
                reason="risk_reduction",
                score=max(score * self.strategy_settings.reduction_target_fraction, 0.0),
                current_weight=current_weight,
                target_weight=reduced_weight,
            )

        if not held and not entry_eligible:
            reason = (
                "defensive_regime"
                if (
                    self.research_profile.regime_layer_enabled
                    and regime_state == "defensive"
                    and not defensive_entry_allowed
                )
                else "entry_filter_failed"
            )
            return self._asset_decision(
                asset=asset,
                action="blocked",
                reason=reason,
                score=0.0,
                current_weight=current_weight,
            )

        if score <= 0:
            fallback_action: AssetAction = "hold" if held else "blocked"
            fallback_reason = "held_supportive" if held else "insufficient_score"
            return self._asset_decision(
                asset=asset,
                action=fallback_action,
                reason=fallback_reason,
                score=max(score, 0.0),
                current_weight=current_weight,
            )

        return self._asset_decision(
            asset=asset,
            action="hold" if held else "enter",
            reason="eligible",
            score=score,
            current_weight=current_weight,
        )

    def _score_asset(
        self,
        *,
        short_momentum: float,
        long_momentum: float,
        short_trend: float,
        long_trend: float,
        relative_strength: float,
        volatility: float,
        breadth_positive: float,
        breadth_above_trend: float,
        held: bool,
    ) -> float:
        score = (
            0.24 * max(short_momentum, 0.0)
            + 0.24 * max(long_momentum, 0.0)
            + 0.18 * max(relative_strength + 0.05, 0.0)
            + 0.14 * max(short_trend, 0.0)
            + 0.10 * max(long_trend, 0.0)
            + 0.05 * breadth_positive
            + 0.05 * breadth_above_trend
            - 0.10 * volatility
        )
        if held:
            score += self.strategy_settings.held_asset_score_bonus
        return max(score, 0.0)

    def _build_target_weights(
        self,
        scores: dict[str, float],
        reserved_weights: dict[str, float],
        exposure_fraction: float,
    ) -> dict[str, float]:
        if exposure_fraction <= 0:
            return {}
        reserved_total = sum(reserved_weights.values())
        if reserved_total >= exposure_fraction:
            if reserved_total <= 0:
                return {}
            scale = exposure_fraction / reserved_total
            return {
                asset: weight * scale
                for asset, weight in reserved_weights.items()
                if weight * scale > 0
            }

        remaining_slots = max(self.backtest_settings.max_positions - len(reserved_weights), 0)
        ranked_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        selected_scores = dict(ranked_scores[:remaining_slots]) if remaining_slots > 0 else {}
        normalized = self._cap_and_normalize(
            selected_scores,
            total_target_weight=max(exposure_fraction - reserved_total, 0.0),
            max_weight=self.backtest_settings.max_asset_weight,
        )
        merged = dict(reserved_weights)
        for asset, weight in normalized.items():
            merged[asset] = min(
                merged.get(asset, 0.0) + weight,
                self.backtest_settings.max_asset_weight,
            )
        return {asset: weight for asset, weight in merged.items() if weight > 0}

    def _finalize_asset_decisions(
        self,
        *,
        portfolio: PortfolioState,
        preliminary: dict[str, AssetDecision],
        target_weights: dict[str, float],
        current_weights: dict[str, float],
    ) -> dict[str, AssetDecision]:
        decisions: dict[str, AssetDecision] = {}
        asset_scope = tuple(
            sorted(set(preliminary) | set(portfolio.positions) | set(target_weights))
        )
        for asset in asset_scope:
            base = preliminary.get(
                asset,
                AssetDecision(
                    asset=asset,
                    action="blocked",
                    reason="not_selected",
                    score=0.0,
                    current_weight=current_weights.get(asset, 0.0),
                    target_weight=0.0,
                ),
            )
            target_weight = target_weights.get(asset, base.target_weight)
            current_weight = current_weights.get(asset, base.current_weight)
            action = base.action
            if asset in portfolio.positions:
                if target_weight <= 0 and base.action != "hold":
                    action = "exit"
                elif target_weight < current_weight - self.backtest_settings.rebalance_threshold:
                    action = "reduce"
                elif target_weight > current_weight + self.backtest_settings.rebalance_threshold:
                    action = "increase"
                else:
                    action = "hold" if base.action not in {"exit", "reduce"} else base.action
            elif target_weight > 0:
                action = "enter"
            decisions[asset] = replace(base, action=action, target_weight=target_weight)
        return decisions

    def _regime_state(self, rows_by_asset: dict[str, dict[str, object]]) -> str:
        if not rows_by_asset:
            return "frozen"
        regimes = {str(row["regime_state"]) for row in rows_by_asset.values()}
        if len(regimes) != 1:
            return "frozen"
        return next(iter(regimes))

    def _freeze_reason(
        self,
        *,
        regime_state: str,
        rows_by_asset: dict[str, dict[str, object]],
        portfolio: PortfolioState,
        prices_by_asset: dict[str, float],
    ) -> str | None:
        if regime_state == "frozen":
            return "regime_frozen"
        if not rows_by_asset:
            return "missing_feature_rows"
        for asset in portfolio.positions:
            if asset not in prices_by_asset:
                return f"missing_price:{asset}"
            if asset not in rows_by_asset:
                return f"missing_signal:{asset}"
        return None

    def _risk_state(
        self,
        *,
        regime_state: str,
        portfolio_drawdown: float,
        freeze_reason: str | None,
    ) -> RiskState:
        if freeze_reason is not None:
            return "frozen"
        if portfolio_drawdown <= -self.strategy_settings.drawdown_catastrophe_threshold:
            return "catastrophe"
        if (
            (
                self.research_profile.regime_layer_enabled
                and regime_state == "defensive"
            )
            or portfolio_drawdown <= -self.strategy_settings.drawdown_reduced_threshold
        ):
            return "reduced_aggressiveness"
        if (
            (
                self.research_profile.regime_layer_enabled
                and regime_state == "neutral"
            )
            or portfolio_drawdown <= -self.strategy_settings.drawdown_caution_threshold
        ):
            return "elevated_caution"
        return "normal"

    def _target_exposure(self, *, regime_state: str, risk_state: RiskState) -> float:
        if not self.research_profile.regime_layer_enabled:
            regime_exposure = self.backtest_settings.constructive_exposure
        elif regime_state == "constructive":
            regime_exposure = self.backtest_settings.constructive_exposure
        elif regime_state == "neutral":
            regime_exposure = self.backtest_settings.neutral_exposure
        elif regime_state == "defensive":
            regime_exposure = self.backtest_settings.defensive_exposure
        else:
            regime_exposure = 0.0

        if risk_state == "elevated_caution":
            multiplier = self.strategy_settings.elevated_caution_exposure_multiplier
        elif risk_state == "reduced_aggressiveness":
            multiplier = self.strategy_settings.reduced_aggressiveness_exposure_multiplier
        elif risk_state == "catastrophe":
            multiplier = self.strategy_settings.catastrophe_exposure_multiplier
        elif risk_state == "frozen":
            multiplier = 0.0
        else:
            multiplier = 1.0
        return regime_exposure * multiplier

    def _current_weights(
        self,
        portfolio: PortfolioState,
        prices_by_asset: dict[str, float],
        current_equity: float,
    ) -> dict[str, float]:
        if current_equity <= 0:
            return {asset: 0.0 for asset in portfolio.positions}
        return {
            asset: (position.quantity * prices_by_asset.get(asset, 0.0)) / current_equity
            for asset, position in portfolio.positions.items()
        }

    def _portfolio_equity(
        self,
        portfolio: PortfolioState,
        prices_by_asset: dict[str, float],
    ) -> float:
        return portfolio.cash_usd + sum(
            position.quantity * prices_by_asset.get(asset, 0.0)
            for asset, position in portfolio.positions.items()
        )

    def _frozen_asset_decisions(
        self,
        portfolio: PortfolioState,
        rows_by_asset: dict[str, dict[str, object]],
    ) -> dict[str, AssetDecision]:
        asset_scope = tuple(sorted(set(rows_by_asset) | set(portfolio.positions)))
        return {
            asset: AssetDecision(
                asset=asset,
                action="hold" if asset in portfolio.positions else "blocked",
                reason="strategy_frozen",
                score=0.0,
                current_weight=0.0,
                target_weight=0.0,
            )
            for asset in asset_scope
        }

    def _cap_and_normalize(
        self,
        scores: dict[str, float],
        *,
        total_target_weight: float,
        max_weight: float,
    ) -> dict[str, float]:
        remaining = dict(scores)
        allocations = {asset: 0.0 for asset in scores}
        remaining_weight = total_target_weight

        while remaining and remaining_weight > 0:
            score_total = sum(remaining.values())
            if score_total <= 0:
                break

            capped_assets: list[str] = []
            for asset, score in remaining.items():
                proposed = remaining_weight * (score / score_total)
                if proposed >= max_weight:
                    allocations[asset] = max_weight
                    remaining_weight -= max_weight
                    capped_assets.append(asset)

            if capped_assets:
                for asset in capped_assets:
                    remaining.pop(asset, None)
                continue

            for asset, score in remaining.items():
                allocations[asset] = remaining_weight * (score / score_total)
            break

        return {asset: weight for asset, weight in allocations.items() if weight > 0}

    def _asset_decision(
        self,
        *,
        asset: str,
        action: AssetAction,
        reason: str,
        score: float,
        current_weight: float,
        target_weight: float = 0.0,
    ) -> AssetDecision:
        return AssetDecision(
            asset=asset,
            action=action,
            reason=reason,
            score=score,
            current_weight=current_weight,
            target_weight=target_weight,
        )

    def _float_value(self, row: dict[str, object], key: str) -> float:
        value = row[key]
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float):
            return float(value)
        return float(str(value))

    def _float_or_default(self, row: dict[str, object], key: str, *, default: float) -> float:
        if key not in row:
            return default
        return self._float_value(row, key)
