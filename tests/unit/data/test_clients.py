"""Unit tests for public market-data clients."""

import httpx

from spotbot.data.clients import BinancePublicClient, CoinbasePublicClient, KrakenPublicClient


def test_kraken_public_client_drops_uncommitted_last_row() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/public/OHLC"
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "XXBTZUSD": [
                        [1704067200, "1", "2", "0.5", "1.5", "1.4", "10", 3],
                        [1704070800, "1.5", "2.5", "1.4", "2.2", "2.0", "8", 2],
                    ],
                    "last": 1704074400,
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.kraken.com")
    rows = KrakenPublicClient(client=client).fetch_ohlc(
        pair="XBTUSD", interval="1h", since=1704067200
    )

    assert len(rows) == 1
    assert rows[0].timestamp == 1704067200
    assert rows[0].source == "kraken_api"


def test_binance_public_client_parses_kline_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                [1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999, "0", 7, "0", "0", "0"],
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.binance.com")
    rows = BinancePublicClient(client=client).fetch_klines(
        symbol="BTCUSDT", interval="1h", start_ts=1704067200, end_ts=1704067200
    )

    assert len(rows) == 1
    assert rows[0].timestamp == 1704067200
    assert rows[0].trade_count == 7


def test_coinbase_public_client_parses_candles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[[1704067200, 0.5, 2.0, 1.0, 1.5, 10.0]])

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.exchange.coinbase.com"
    )
    rows = CoinbasePublicClient(client=client).fetch_candles(
        product_id="BTC-USD", interval="1h", start_ts=1704067200, end_ts=1704067200
    )

    assert len(rows) == 1
    assert rows[0].timestamp == 1704067200
    assert rows[0].source == "coinbase_fallback"