"""Public market data clients for Kraken, Binance, and Coinbase."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tradebot.data.models import Candle, Interval


class KrakenPublicClient:
    """Minimal public client for Kraken OHLC data."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url="https://api.kraken.com", timeout=30.0)

    def fetch_ohlc(self, pair: str, interval: Interval, since: int) -> list[Candle]:
        """Fetch committed OHLC candles from Kraken."""
        response = self._client.get(
            "/0/public/OHLC",
            params={"pair": pair, "interval": interval_minutes(interval), "since": since},
        )
        response.raise_for_status()
        payload = response.json()
        result = payload["result"]
        pair_key = next(key for key in result if key != "last")
        rows = result[pair_key]
        if rows:
            rows = rows[:-1]
        return [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[6]),
                trade_count=int(row[7]),
                source="kraken_api",
            )
            for row in rows
        ]

    def fetch_ohlc_range(
        self,
        pair: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Fetch Kraken OHLC candles for a bounded range, paging until exhausted."""
        if start_ts > end_ts:
            return []

        results: dict[int, Candle] = {}
        step = interval_seconds(interval)
        cursor = start_ts
        while cursor <= end_ts:
            page = self.fetch_ohlc(pair=pair, interval=interval, since=max(cursor - step, 0))
            page = [candle for candle in page if start_ts <= candle.timestamp <= end_ts]
            if not page:
                break

            for candle in page:
                results[candle.timestamp] = candle

            next_cursor = max(candle.timestamp for candle in page) + step
            if next_cursor <= cursor:
                break
            cursor = next_cursor

        return [results[timestamp] for timestamp in sorted(results)]


class BinancePublicClient:
    """Public Binance spot client for kline fallback retrieval."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url="https://api.binance.com", timeout=30.0)

    def fetch_klines(
        self, symbol: str, interval: Interval, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Fetch paginated Binance klines for the requested time range."""
        results: list[Candle] = []
        current_start = start_ts * 1000
        end_ms = end_ts * 1000
        while current_start <= end_ms:
            response = self._client.get(
                "/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": current_start,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                break

            for row in rows:
                results.append(
                    Candle(
                        timestamp=int(row[0]) // 1000,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                        trade_count=int(row[8]),
                        source="binance_fallback",
                    )
                )

            current_start = (int(rows[-1][0]) // 1000 + interval_seconds(interval)) * 1000
        return results


class CoinbasePublicClient:
    """Public Coinbase Exchange client for candle fallback retrieval."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            base_url="https://api.exchange.coinbase.com", timeout=30.0
        )

    def fetch_candles(
        self, product_id: str, interval: Interval, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Fetch paginated Coinbase candles for the requested time range."""
        results: list[Candle] = []
        step = interval_seconds(interval)
        cursor = start_ts
        max_points = 300
        window = step * max_points

        while cursor <= end_ts:
            window_end = min(end_ts, cursor + window - step)
            response = self._client.get(
                f"/products/{product_id}/candles",
                params={
                    "granularity": step,
                    "start": datetime.fromtimestamp(cursor, tz=UTC).isoformat(),
                    "end": datetime.fromtimestamp(window_end, tz=UTC).isoformat(),
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                cursor = window_end + step
                continue

            parsed = [
                Candle(
                    timestamp=int(row[0]),
                    low=float(row[1]),
                    high=float(row[2]),
                    open=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    trade_count=1,
                    source="coinbase_fallback",
                )
                for row in rows
            ]
            results.extend(sorted(parsed, key=lambda candle: candle.timestamp))
            cursor = max(candle.timestamp for candle in parsed) + step
        return results


def interval_minutes(interval: Interval) -> int:
    """Convert canonical interval names to Kraken minutes."""
    if interval == "1h":
        return 60
    if interval == "1d":
        return 1440
    raise ValueError(f"unsupported interval: {interval}")


def interval_seconds(interval: Interval) -> int:
    """Convert canonical interval names to seconds."""
    if interval == "1h":
        return 3600
    if interval == "1d":
        return 86400
    raise ValueError(f"unsupported interval: {interval}")