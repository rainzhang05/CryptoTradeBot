"""Streaming aggregation from raw trades to canonical candles."""

from __future__ import annotations

from dataclasses import dataclass

from spotbot.data.models import INTERVAL_SECONDS, Candle, Interval, RawTrade


@dataclass
class CandleAccumulator:
    """Incrementally aggregate sorted trades into candles."""

    interval: Interval
    source: str
    current_bucket: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float = 0.0
    trade_count: int = 0

    def add_trade(self, trade: RawTrade) -> Candle | None:
        """Add a trade and optionally emit the finished previous candle."""
        bucket = bucket_start(trade.timestamp, self.interval)
        if self.current_bucket is None:
            self._start_bucket(bucket, trade)
            return None

        if bucket < self.current_bucket:
            raise ValueError("trades must be sorted by ascending timestamp")

        if bucket == self.current_bucket:
            self._update_bucket(trade)
            return None

        finished = self._emit_current()
        self._start_bucket(bucket, trade)
        return finished

    def finish(self) -> Candle | None:
        """Emit the current candle, if any."""
        if self.current_bucket is None:
            return None
        return self._emit_current()

    def _start_bucket(self, bucket: int, trade: RawTrade) -> None:
        self.current_bucket = bucket
        self.open = trade.price
        self.high = trade.price
        self.low = trade.price
        self.close = trade.price
        self.volume = trade.volume
        self.trade_count = 1

    def _update_bucket(self, trade: RawTrade) -> None:
        assert self.high is not None
        assert self.low is not None
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.close = trade.price
        self.volume += trade.volume
        self.trade_count += 1

    def _emit_current(self) -> Candle:
        assert self.current_bucket is not None
        assert self.open is not None
        assert self.high is not None
        assert self.low is not None
        assert self.close is not None

        candle = Candle(
            timestamp=self.current_bucket,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            trade_count=self.trade_count,
            source=self.source,
        )
        self.current_bucket = None
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = 0.0
        self.trade_count = 0
        return candle


def bucket_start(timestamp: int, interval: Interval) -> int:
    """Return the bucket start timestamp for an interval."""
    seconds = INTERVAL_SECONDS[interval]
    return timestamp - (timestamp % seconds)