"""Deterministic feature generation for research datasets."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from typing import cast

from tradebot.config import ResearchSettings
from tradebot.data.models import Candle

REGIME_STATES = ("constructive", "neutral", "defensive", "frozen")


def feature_column_names(
    settings: ResearchSettings,
    *,
    include_dynamic_fields: bool = False,
) -> list[str]:
    """Return the stable feature column order for a derived dataset."""
    columns = ["asset", "timestamp"]
    if include_dynamic_fields:
        columns.extend(["asset_age_days", "active_universe_count"])
    columns.extend(f"momentum_{window}d" for window in settings.momentum_windows_days)
    columns.extend(f"trend_gap_{window}d" for window in settings.trend_windows_days)
    columns.extend(
        f"realized_volatility_{window}d" for window in settings.volatility_windows_days
    )
    columns.extend(
        [
            f"relative_strength_{settings.relative_strength_window_days}d",
            f"universe_breadth_positive_{settings.breadth_window_days}d",
            f"universe_breadth_above_trend_{settings.breadth_window_days}d",
            f"avg_dollar_volume_{settings.dollar_volume_window_days}d",
            f"avg_trade_count_{settings.dollar_volume_window_days}d",
            "liquidity_sanity_flag",
            f"btc_momentum_{settings.breadth_window_days}d",
            f"btc_trend_gap_{settings.trend_windows_days[0]}d",
            f"btc_realized_volatility_{settings.volatility_windows_days[0]}d",
            "latest_source_is_kraken",
            f"kraken_source_ratio_{settings.source_window_days}d",
            f"binance_source_ratio_{settings.source_window_days}d",
            f"coinbase_source_ratio_{settings.source_window_days}d",
            f"fallback_source_ratio_{settings.source_window_days}d",
            f"source_confidence_{settings.source_window_days}d",
            "regime_state",
        ]
    )
    columns.extend(f"regime_{state}" for state in REGIME_STATES)
    return columns


def build_feature_rows(
    candles_by_asset: dict[str, list[Candle]],
    settings: ResearchSettings,
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    """Build deterministic feature rows from aligned daily candles."""
    return _build_rows(candles_by_asset, settings)


def build_signal_rows(
    candles_by_asset: dict[str, list[Candle]],
    settings: ResearchSettings,
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    """Build point-in-time signal rows."""
    return _build_rows(candles_by_asset, settings)


def build_dynamic_feature_rows(
    candles_by_asset: dict[str, list[Candle]],
    settings: ResearchSettings,
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    """Build feature rows using a Kraken-only dynamic active universe."""
    return _build_dynamic_rows(candles_by_asset, settings)


def build_dynamic_signal_rows(
    candles_by_asset: dict[str, list[Candle]],
    settings: ResearchSettings,
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    """Build point-in-time signal rows for the dynamic active universe."""
    return _build_dynamic_rows(candles_by_asset, settings)


def _build_rows(
    candles_by_asset: dict[str, list[Candle]],
    settings: ResearchSettings,
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    """Build deterministic feature rows from aligned daily candles."""
    aligned = _align_candles(candles_by_asset)
    series = {asset: _AssetSeries(candles) for asset, candles in aligned.items()}
    assets = tuple(aligned)
    timestamps = [candle.timestamp for candle in next(iter(aligned.values()))]

    rows: list[dict[str, object]] = []
    row_counts = {asset: 0 for asset in assets}

    rs_window = settings.relative_strength_window_days
    breadth_window = settings.breadth_window_days
    shortest_trend_window = settings.trend_windows_days[0]
    shortest_vol_window = settings.volatility_windows_days[0]

    for index, timestamp in enumerate(timestamps):
        momentum_by_asset = {
            asset: series[asset].momentum(rs_window, index) for asset in assets
        }
        breadth_positive = _breadth_positive(momentum_by_asset)
        breadth_above_trend = _breadth_above_trend(series, shortest_trend_window, index)

        btc_series = series["BTC"]
        btc_momentum = btc_series.momentum(breadth_window, index)
        btc_trend_gap = btc_series.trend_gap(shortest_trend_window, index)
        btc_volatility = btc_series.realized_volatility(shortest_vol_window, index)
        btc_source_confidence = btc_series.source_ratio(
            "kraken", settings.source_window_days, index
        )
        regime_state = classify_regime(
            btc_momentum=btc_momentum,
            btc_trend_gap=btc_trend_gap,
            breadth_positive=breadth_positive,
            btc_source_confidence=btc_source_confidence,
        )

        universe_average_momentum = _average_or_none(momentum_by_asset.values())
        if universe_average_momentum is None:
            continue

        for asset in assets:
            asset_series = series[asset]
            row = _build_asset_row(
                asset=asset,
                timestamp=timestamp,
                asset_series=asset_series,
                settings=settings,
                index=index,
                regime_state=regime_state,
                breadth_positive=breadth_positive,
                breadth_above_trend=breadth_above_trend,
                btc_momentum=btc_momentum,
                btc_trend_gap=btc_trend_gap,
                btc_volatility=btc_volatility,
                universe_average_momentum=universe_average_momentum,
                asset_momentum=momentum_by_asset[asset],
            )
            if row is None:
                continue
            rows.append(row)
            row_counts[asset] += 1

    stats = {
        asset: {
            "first_timestamp": int(
                next(
                (cast(int, row["timestamp"]) for row in rows if row["asset"] == asset),
                0,
                )
            ),
            "last_timestamp": int(
                next(
                (cast(int, row["timestamp"]) for row in reversed(rows) if row["asset"] == asset),
                0,
                )
            ),
            "row_count": row_counts[asset],
        }
        for asset in assets
    }
    return rows, stats


def _build_dynamic_rows(
    candles_by_asset: dict[str, list[Candle]],
    settings: ResearchSettings,
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    series = {
        asset: _AssetSeries(candles)
        for asset, candles in candles_by_asset.items()
        if candles
    }
    if not series:
        raise ValueError("No canonical candles available for feature generation")

    assets = tuple(series)
    timestamps = sorted(
        {
            candle.timestamp
            for asset_candles in candles_by_asset.values()
            for candle in asset_candles
        }
    )
    index_by_asset = {
        asset: {candle.timestamp: index for index, candle in enumerate(candles_by_asset[asset])}
        for asset in assets
    }
    if "BTC" not in series:
        raise ValueError("Feature generation requires BTC for regime classification")

    rows: list[dict[str, object]] = []
    row_counts = {asset: 0 for asset in assets}
    rs_window = settings.relative_strength_window_days
    breadth_window = settings.breadth_window_days
    shortest_trend_window = settings.trend_windows_days[0]
    shortest_vol_window = settings.volatility_windows_days[0]
    btc_series = series["BTC"]
    btc_index_by_timestamp = index_by_asset["BTC"]

    for timestamp in timestamps:
        btc_index = btc_index_by_timestamp.get(timestamp)
        if btc_index is None:
            continue

        active_assets = tuple(
            asset for asset in assets if timestamp in index_by_asset[asset]
        )
        active_universe_count = len(active_assets)
        if active_universe_count <= 0:
            continue

        momentum_by_asset = {
            asset: series[asset].momentum(rs_window, index_by_asset[asset][timestamp])
            for asset in active_assets
        }
        breadth_positive = _breadth_positive_available(momentum_by_asset)
        breadth_above_trend = _breadth_above_trend_dynamic(
            series,
            index_by_asset,
            active_assets,
            shortest_trend_window,
            timestamp,
        )

        btc_momentum = btc_series.momentum(breadth_window, btc_index)
        btc_trend_gap = btc_series.trend_gap(shortest_trend_window, btc_index)
        btc_volatility = btc_series.realized_volatility(shortest_vol_window, btc_index)
        btc_source_confidence = btc_series.source_ratio(
            "kraken",
            settings.source_window_days,
            btc_index,
        )
        regime_state = classify_regime(
            btc_momentum=btc_momentum,
            btc_trend_gap=btc_trend_gap,
            breadth_positive=breadth_positive,
            btc_source_confidence=btc_source_confidence,
        )

        universe_average_momentum = _average_available_or_none(momentum_by_asset.values())
        if universe_average_momentum is None:
            continue

        for asset in active_assets:
            asset_index = index_by_asset[asset][timestamp]
            asset_series = series[asset]
            asset_age_days = int(
                (timestamp - asset_series.candles[0].timestamp) / 86_400
            )
            row = _build_asset_row(
                asset=asset,
                timestamp=timestamp,
                asset_series=asset_series,
                settings=settings,
                index=asset_index,
                regime_state=regime_state,
                breadth_positive=breadth_positive,
                breadth_above_trend=breadth_above_trend,
                btc_momentum=btc_momentum,
                btc_trend_gap=btc_trend_gap,
                btc_volatility=btc_volatility,
                universe_average_momentum=universe_average_momentum,
                asset_momentum=momentum_by_asset[asset],
                asset_age_days=asset_age_days,
                active_universe_count=active_universe_count,
            )
            if row is None:
                continue
            rows.append(row)
            row_counts[asset] += 1

    stats = {
        asset: {
            "first_timestamp": int(
                next(
                    (cast(int, row["timestamp"]) for row in rows if row["asset"] == asset),
                    0,
                )
            ),
            "last_timestamp": int(
                next(
                    (
                        cast(int, row["timestamp"])
                        for row in reversed(rows)
                        if row["asset"] == asset
                    ),
                    0,
                )
            ),
            "row_count": row_counts[asset],
        }
        for asset in assets
    }
    return rows, stats


def classify_regime(
    *,
    btc_momentum: float | None,
    btc_trend_gap: float | None,
    breadth_positive: float | None,
    btc_source_confidence: float | None,
) -> str:
    """Classify the BTC-led market regime for one decision date."""
    if (
        btc_source_confidence is None
        or btc_source_confidence < 0.8
        or btc_momentum is None
        or btc_trend_gap is None
        or breadth_positive is None
    ):
        return "frozen"

    if btc_momentum > 0 and btc_trend_gap > 0 and breadth_positive >= 0.6:
        return "constructive"

    if btc_momentum < -0.05 or btc_trend_gap < -0.03 or breadth_positive <= 0.35:
        return "defensive"

    return "neutral"


def _build_asset_row(
    *,
    asset: str,
    timestamp: int,
    asset_series: _AssetSeries,
    settings: ResearchSettings,
    index: int,
    regime_state: str,
    breadth_positive: float | None,
    breadth_above_trend: float | None,
    btc_momentum: float | None,
    btc_trend_gap: float | None,
    btc_volatility: float | None,
    universe_average_momentum: float | None,
    asset_momentum: float | None,
    asset_age_days: int | None = None,
    active_universe_count: int | None = None,
) -> dict[str, object] | None:
    momentum_values = {
        window: asset_series.momentum(window, index) for window in settings.momentum_windows_days
    }
    trend_values = {
        window: asset_series.trend_gap(window, index) for window in settings.trend_windows_days
    }
    volatility_values = {
        window: asset_series.realized_volatility(window, index)
        for window in settings.volatility_windows_days
    }

    avg_dollar_volume = asset_series.average_dollar_volume(
        settings.dollar_volume_window_days, index
    )
    avg_trade_count = asset_series.average_trade_count(settings.dollar_volume_window_days, index)
    latest_source_is_kraken = 1.0 if asset_series.is_primary_source(index) else 0.0
    kraken_ratio = asset_series.source_ratio("kraken", settings.source_window_days, index)
    binance_ratio = asset_series.source_ratio("binance", settings.source_window_days, index)
    coinbase_ratio = asset_series.source_ratio("coinbase", settings.source_window_days, index)
    fallback_ratio = None if kraken_ratio is None else max(0.0, 1.0 - kraken_ratio)
    source_confidence = kraken_ratio

    required_values = [
        *momentum_values.values(),
        *trend_values.values(),
        *volatility_values.values(),
        avg_dollar_volume,
        avg_trade_count,
        breadth_positive,
        breadth_above_trend,
        btc_momentum,
        btc_trend_gap,
        btc_volatility,
        asset_momentum,
        universe_average_momentum,
        kraken_ratio,
        binance_ratio,
        coinbase_ratio,
        fallback_ratio,
        source_confidence,
    ]
    if any(value is None for value in required_values):
        return None

    assert avg_dollar_volume is not None
    assert avg_trade_count is not None
    assert breadth_positive is not None
    assert breadth_above_trend is not None
    assert btc_momentum is not None
    assert btc_trend_gap is not None
    assert btc_volatility is not None
    assert universe_average_momentum is not None
    assert asset_momentum is not None
    assert kraken_ratio is not None
    assert binance_ratio is not None
    assert coinbase_ratio is not None
    assert fallback_ratio is not None
    assert source_confidence is not None
    row: dict[str, object] = {
        "asset": asset,
        "timestamp": timestamp,
        f"relative_strength_{settings.relative_strength_window_days}d": asset_momentum
        - universe_average_momentum,
        f"universe_breadth_positive_{settings.breadth_window_days}d": breadth_positive,
        f"universe_breadth_above_trend_{settings.breadth_window_days}d": breadth_above_trend,
        f"avg_dollar_volume_{settings.dollar_volume_window_days}d": avg_dollar_volume,
        f"avg_trade_count_{settings.dollar_volume_window_days}d": avg_trade_count,
        "liquidity_sanity_flag": 1.0 if avg_dollar_volume > 0 and avg_trade_count > 0 else 0.0,
        f"btc_momentum_{settings.breadth_window_days}d": btc_momentum,
        f"btc_trend_gap_{settings.trend_windows_days[0]}d": btc_trend_gap,
        f"btc_realized_volatility_{settings.volatility_windows_days[0]}d": btc_volatility,
        "latest_source_is_kraken": latest_source_is_kraken,
        f"kraken_source_ratio_{settings.source_window_days}d": kraken_ratio,
        f"binance_source_ratio_{settings.source_window_days}d": binance_ratio,
        f"coinbase_source_ratio_{settings.source_window_days}d": coinbase_ratio,
        f"fallback_source_ratio_{settings.source_window_days}d": fallback_ratio,
        f"source_confidence_{settings.source_window_days}d": source_confidence,
        "regime_state": regime_state,
    }
    if asset_age_days is not None:
        row["asset_age_days"] = asset_age_days
    if active_universe_count is not None:
        row["active_universe_count"] = active_universe_count

    for window, value in momentum_values.items():
        row[f"momentum_{window}d"] = value
    for window, value in trend_values.items():
        row[f"trend_gap_{window}d"] = value
    for window, value in volatility_values.items():
        row[f"realized_volatility_{window}d"] = value
    for state in REGIME_STATES:
        row[f"regime_{state}"] = 1 if regime_state == state else 0
    return row


def _align_candles(candles_by_asset: dict[str, list[Candle]]) -> dict[str, list[Candle]]:
    timestamp_sets = [
        {candle.timestamp for candle in candles}
        for candles in candles_by_asset.values()
        if candles
    ]
    if not timestamp_sets:
        raise ValueError("No canonical candles available for feature generation")

    common_timestamps = set.intersection(*timestamp_sets)
    if not common_timestamps:
        raise ValueError("Selected assets do not share any aligned daily timestamps")

    ordered_timestamps = sorted(common_timestamps)
    aligned: dict[str, list[Candle]] = {}
    for asset, candles in candles_by_asset.items():
        by_timestamp = {candle.timestamp: candle for candle in candles}
        aligned[asset] = [by_timestamp[timestamp] for timestamp in ordered_timestamps]
    return aligned


def _breadth_positive(momentum_by_asset: dict[str, float | None]) -> float | None:
    values = [value for value in momentum_by_asset.values() if value is not None]
    if len(values) != len(momentum_by_asset):
        return None
    positives = sum(1 for value in values if value > 0)
    return positives / len(values)


def _breadth_positive_available(momentum_by_asset: dict[str, float | None]) -> float | None:
    values = [value for value in momentum_by_asset.values() if value is not None]
    if not values:
        return None
    positives = sum(1 for value in values if value > 0)
    return positives / len(values)


def _breadth_above_trend(
    series: dict[str, _AssetSeries],
    window: int,
    index: int,
) -> float | None:
    flags: list[float] = []
    for asset_series in series.values():
        trend_gap = asset_series.trend_gap(window, index)
        if trend_gap is None:
            return None
        flags.append(1.0 if trend_gap > 0 else 0.0)
    return sum(flags) / len(flags)


def _breadth_above_trend_dynamic(
    series: dict[str, _AssetSeries],
    index_by_asset: dict[str, dict[int, int]],
    active_assets: tuple[str, ...],
    window: int,
    timestamp: int,
) -> float | None:
    flags: list[float] = []
    for asset in active_assets:
        index = index_by_asset[asset].get(timestamp)
        if index is None:
            continue
        trend_gap = series[asset].trend_gap(window, index)
        if trend_gap is None:
            continue
        flags.append(1.0 if trend_gap > 0 else 0.0)
    if not flags:
        return None
    return sum(flags) / len(flags)


def _average_or_none(values: Iterable[float | None]) -> float | None:
    collected = list(values)
    numeric_values = [value for value in collected if value is not None]
    if not numeric_values:
        return None
    if len(numeric_values) != len(collected):
        return None
    return sum(numeric_values) / len(numeric_values)


def _average_available_or_none(values: Iterable[float | None]) -> float | None:
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


class _AssetSeries:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.closes = [candle.close for candle in candles]
        self.lows = [candle.low for candle in candles]
        self.volumes = [candle.volume for candle in candles]
        self.trade_counts = [candle.trade_count for candle in candles]
        self.sources = [candle.source for candle in candles]
        self.daily_returns = [
            None,
            *[
                (self.closes[index] / self.closes[index - 1]) - 1
                for index in range(1, len(self.closes))
            ],
        ]

    def momentum(self, window: int, index: int) -> float | None:
        if index < window:
            return None
        base_close = self.closes[index - window]
        return (self.closes[index] / base_close) - 1

    def trend_gap(self, window: int, index: int) -> float | None:
        if index + 1 < window:
            return None
        values = self.closes[index - window + 1 : index + 1]
        average = sum(values) / len(values)
        return (self.closes[index] / average) - 1

    def realized_volatility(self, window: int, index: int) -> float | None:
        if index < window:
            return None
        returns = self.daily_returns[index - window + 1 : index + 1]
        if any(value is None for value in returns):
            return None
        realized_returns = [float(value) for value in returns if value is not None]
        mean_return = sum(realized_returns) / len(realized_returns)
        variance = sum((value - mean_return) ** 2 for value in realized_returns) / len(
            realized_returns
        )
        return math.sqrt(variance) * math.sqrt(365)

    def average_dollar_volume(self, window: int, index: int) -> float | None:
        if index + 1 < window:
            return None
        values = [
            self.closes[position] * self.volumes[position]
            for position in range(index - window + 1, index + 1)
        ]
        return sum(values) / len(values)

    def average_trade_count(self, window: int, index: int) -> float | None:
        if index + 1 < window:
            return None
        values = self.trade_counts[index - window + 1 : index + 1]
        return sum(values) / len(values)

    def forward_return(self, window: int, index: int) -> float | None:
        if index + window >= len(self.closes):
            return None
        return (self.closes[index + window] / self.closes[index]) - 1

    def forward_min_low_return(self, window: int, index: int) -> float | None:
        if index + window >= len(self.lows):
            return None
        minimum_low = min(self.lows[index + 1 : index + window + 1])
        return (minimum_low / self.closes[index]) - 1

    def is_primary_source(self, index: int) -> bool:
        return self.sources[index].startswith("kraken")

    def source_ratio(self, source_name: str, window: int, index: int) -> float | None:
        if index + 1 < window:
            return None
        window_sources = self.sources[index - window + 1 : index + 1]
        counts = Counter(window_sources)
        match_count = sum(
            count
            for source, count in counts.items()
            if self._source_matches(source, source_name)
        )
        return match_count / len(window_sources)

    @staticmethod
    def _source_matches(source: str, source_name: str) -> bool:
        return source == source_name or source.startswith(f"{source_name}_")
