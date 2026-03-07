"""Deterministic allocation policy for backtest and simulate mode."""

from __future__ import annotations

from tradebot.config import AppConfig


def build_target_weights(
    *,
    timestamp: int,
    rows_by_asset: dict[str, dict[str, object]],
    config: AppConfig,
) -> tuple[str, float, dict[str, float], dict[str, float]]:
    """Build target portfolio weights from phase 3 feature rows."""
    regime_state = _regime_state(rows_by_asset)
    exposure_fraction = _exposure_fraction(config, regime_state)
    scores: dict[str, float] = {}

    for asset, row in rows_by_asset.items():
        score = _score_asset(row=row, config=config)
        if score > 0:
            scores[asset] = score

    if not scores or exposure_fraction <= 0:
        return regime_state, exposure_fraction, {}, scores

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    limited_scores = dict(ranked[: config.backtest.max_positions])
    target_weights = _cap_and_normalize(
        limited_scores,
        total_target_weight=exposure_fraction,
        max_weight=config.backtest.max_asset_weight,
    )
    return regime_state, exposure_fraction, target_weights, scores


def _score_asset(*, row: dict[str, object], config: AppConfig) -> float:
    source_confidence = _float_value(
        row, f"source_confidence_{config.research.source_window_days}d"
    )
    liquidity_flag = _float_value(row, "liquidity_sanity_flag")
    regime_state = str(row["regime_state"])
    if source_confidence < 0.8 or liquidity_flag < 1 or regime_state == "frozen":
        return 0.0

    short_momentum = _float_value(
        row, f"momentum_{config.research.momentum_windows_days[0]}d"
    )
    long_momentum = _float_value(
        row, f"momentum_{config.research.momentum_windows_days[-1]}d"
    )
    relative_strength = _float_value(
        row, f"relative_strength_{config.research.relative_strength_window_days}d"
    )
    short_trend = _float_value(row, f"trend_gap_{config.research.trend_windows_days[0]}d")
    long_trend = _float_value(row, f"trend_gap_{config.research.trend_windows_days[-1]}d")
    volatility = _float_value(
        row, f"realized_volatility_{config.research.volatility_windows_days[0]}d"
    )
    score = (
        0.25 * short_momentum
        + 0.25 * long_momentum
        + 0.2 * relative_strength
        + 0.15 * short_trend
        + 0.1 * long_trend
        - 0.15 * volatility
    )
    return max(score, 0.0)


def _regime_state(rows_by_asset: dict[str, dict[str, object]]) -> str:
    if not rows_by_asset:
        return "frozen"
    return str(next(iter(rows_by_asset.values()))["regime_state"])


def _exposure_fraction(config: AppConfig, regime_state: str) -> float:
    if regime_state == "constructive":
        return config.backtest.constructive_exposure
    if regime_state == "neutral":
        return config.backtest.neutral_exposure
    if regime_state == "defensive":
        return config.backtest.defensive_exposure
    return 0.0


def _cap_and_normalize(
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


def _float_value(row: dict[str, object], key: str) -> float:
    value = row[key]
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    return float(str(value))