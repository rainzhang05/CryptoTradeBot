"""Unit tests for canonical candle integrity checks."""

from pathlib import Path

from spotbot.data.integrity import check_candles


def test_check_candles_detects_missing_interval(tmp_path: Path) -> None:
    candle_file = tmp_path / "candles_1h.csv"
    candle_file.write_text(
        "timestamp,open,high,low,close,volume,trade_count,source\n"
        "1704067200,1,2,1,2,10,2,kraken_raw\n"
        "1704074400,2,3,2,3,8,1,kraken_raw\n",
        encoding="utf-8",
    )

    result = check_candles(asset="BTC", interval="1h", path=candle_file)

    assert result.missing_intervals == 1
    assert result.duplicate_timestamps == 0
    assert result.out_of_order_timestamps == 0