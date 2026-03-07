"""Unit tests for trade aggregation into candles."""

from tradebot.data.aggregation import CandleAccumulator
from tradebot.data.models import RawTrade


def test_candle_accumulator_rolls_hour_bucket() -> None:
    aggregator = CandleAccumulator(interval="1h", source="kraken_raw")

    assert aggregator.add_trade(RawTrade(timestamp=1704067200, price=42000.0, volume=1.0)) is None
    assert aggregator.add_trade(RawTrade(timestamp=1704069000, price=43000.0, volume=2.0)) is None
    emitted = aggregator.add_trade(RawTrade(timestamp=1704070800, price=42500.0, volume=3.0))

    assert emitted is not None
    assert emitted.timestamp == 1704067200
    assert emitted.open == 42000.0
    assert emitted.high == 43000.0
    assert emitted.low == 42000.0
    assert emitted.close == 43000.0
    assert emitted.volume == 3.0
    assert emitted.trade_count == 2


def test_candle_accumulator_rejects_out_of_order_trade() -> None:
    aggregator = CandleAccumulator(interval="1h", source="kraken_raw")
    aggregator.add_trade(RawTrade(timestamp=1704070800, price=42500.0, volume=1.0))

    try:
        aggregator.add_trade(RawTrade(timestamp=1704067200, price=42000.0, volume=1.0))
    except ValueError as exc:
        assert "sorted" in str(exc)
    else:
        raise AssertionError("expected out-of-order trade to raise ValueError")