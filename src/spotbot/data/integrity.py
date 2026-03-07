"""Integrity checks for canonical candle files."""

from __future__ import annotations

import csv
from pathlib import Path

from spotbot.data.models import Candle, IntegrityResult, Interval, INTERVAL_SECONDS


def read_candles(path: Path) -> list[Candle]:
    """Read canonical candles from CSV."""
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            candles.append(
                Candle(
                    timestamp=int(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    trade_count=int(row["trade_count"]),
                    source=row["source"],
                )
            )
    return candles


def check_candles(asset: str, interval: Interval, path: Path) -> IntegrityResult:
    """Validate ordering and basic integrity for a canonical candle file."""
    candles = read_candles(path)
    duplicate_timestamps = 0
    out_of_order_timestamps = 0
    missing_intervals = 0
    non_positive_rows = 0
    previous_timestamp: int | None = None
    step = INTERVAL_SECONDS[interval]

    for candle in candles:
        if (
            candle.open <= 0
            or candle.high <= 0
            or candle.low <= 0
            or candle.close <= 0
            or candle.volume < 0
            or candle.trade_count <= 0
        ):
            non_positive_rows += 1

        if previous_timestamp is not None:
            if candle.timestamp == previous_timestamp:
                duplicate_timestamps += 1
            elif candle.timestamp < previous_timestamp:
                out_of_order_timestamps += 1
            elif candle.timestamp > previous_timestamp + step:
                missing_intervals += ((candle.timestamp - previous_timestamp) // step) - 1
        previous_timestamp = candle.timestamp

    return IntegrityResult(
        asset=asset,
        interval=interval,
        candle_count=len(candles),
        first_timestamp=candles[0].timestamp if candles else None,
        last_timestamp=candles[-1].timestamp if candles else None,
        duplicate_timestamps=duplicate_timestamps,
        out_of_order_timestamps=out_of_order_timestamps,
        missing_intervals=missing_intervals,
        non_positive_rows=non_positive_rows,
        file_path=str(path),
    )