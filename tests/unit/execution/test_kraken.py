"""Unit tests for the Kraken live REST client."""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx

from tradebot.execution.kraken import KrakenClient, KrakenClientError, kraken_signature


def test_kraken_signature_matches_official_auth_example() -> None:
    payload = {
        "nonce": "1616492376594",
        "ordertype": "limit",
        "pair": "XBTUSD",
        "price": 37500,
        "type": "buy",
        "volume": 1.25,
    }

    signature = kraken_signature(
        "/0/private/AddOrder",
        payload,
        "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==",
    )

    assert (
        signature
        == (
            "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
        )
    )


def test_public_endpoints_parse_system_status_ticker_and_pair_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/0/public/SystemStatus":
            return httpx.Response(
                200,
                json={"error": [], "result": {"status": "online", "timestamp": "12345"}},
            )
        if request.url.path == "/0/public/Ticker":
            assert request.url.params["pair"] == "ETHUSD,XBTUSD"
            return httpx.Response(
                200,
                json={
                    "error": [],
                    "result": {
                        "XXBTZUSD": {"altname": "XBTUSD", "c": ["45000.1", "1"]},
                        "XETHZUSD": {"altname": "ETHUSD", "c": ["2500.5", "1"]},
                    },
                },
            )
        if request.url.path == "/0/public/AssetPairs":
            assert request.url.params["pair"] == "ETHUSD,XBTUSD"
            return httpx.Response(
                200,
                json={
                    "error": [],
                    "result": {
                        "XXBTZUSD": {
                            "altname": "XBTUSD",
                            "wsname": "XBT/USD",
                            "status": "online",
                            "lot_decimals": 8,
                            "ordermin": "0.00005",
                            "costmin": "0.5",
                        },
                        "XETHZUSD": {
                            "altname": "ETHUSD",
                            "wsname": "ETH/USD",
                            "status": "online",
                            "lot_decimals": 8,
                            "ordermin": "0.001",
                            "costmin": "0.5",
                        },
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = KrakenClient(
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.kraken.com",
        )
    )

    assert client.get_system_status()["status"] == "online"
    assert client.get_ticker(["ETHUSD", "XBTUSD"]) == {"XBTUSD": 45000.1, "ETHUSD": 2500.5}
    asset_pairs = client.get_asset_pairs(["ETHUSD", "XBTUSD"])
    assert asset_pairs["XBTUSD"].ordermin == 0.00005
    assert asset_pairs["ETHUSD"].wsname == "ETH/USD"


def test_private_endpoints_send_auth_headers_and_parse_results() -> None:
    seen_requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = urllib.parse.parse_qs(request.content.decode("utf-8"))
        payload = {key: value[0] for key, value in body.items()}
        seen_requests.append((request.url.path, payload, dict(request.headers)))

        if request.url.path == "/0/private/Balance":
            return httpx.Response(
                200,
                json={"error": [], "result": {"ZUSD": "1000.5", "XXBT": "0.25"}},
            )
        if request.url.path == "/0/private/OpenOrders":
            return httpx.Response(
                200,
                json={
                    "error": [],
                    "result": {
                        "open": {
                            "OID123": {
                                "status": "open",
                                "descr": {
                                    "pair": "XBTUSD",
                                    "type": "buy",
                                    "ordertype": "market",
                                    "price": "0",
                                },
                                "vol": "0.5",
                                "vol_exec": "0.1",
                                "cost": "4500",
                                "fee": "11.7",
                                "opentm": "1700000000",
                            }
                        }
                    },
                },
            )
        if request.url.path == "/0/private/AddOrder":
            return httpx.Response(
                200,
                json={
                    "error": [],
                    "result": {
                        "descr": {"order": "buy 0.1 XBTUSD @ market"},
                        "txid": ["OID456"],
                    },
                },
            )
        if request.url.path == "/0/private/QueryOrders":
            return httpx.Response(
                200,
                json={
                    "error": [],
                    "result": {
                        "OID456": {
                            "status": "closed",
                            "descr": {
                                "pair": "XBTUSD",
                                "type": "buy",
                                "ordertype": "market",
                                "price": "0",
                            },
                            "vol": "0.1",
                            "vol_exec": "0.1",
                            "price": "46000",
                            "cost": "4600",
                            "fee": "11.96",
                            "opentm": "1700000100",
                            "closetm": "1700000102",
                        }
                    },
                },
            )
        if request.url.path == "/0/private/CancelOrder":
            return httpx.Response(200, json={"error": [], "result": {"count": 1}})
        if request.url.path == "/0/private/CancelAllOrdersAfter":
            return httpx.Response(
                200,
                json={
                    "error": [],
                    "result": {
                        "currentTime": "2026-03-08T12:00:00Z",
                        "triggerTime": "2026-03-08T12:01:00Z",
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = KrakenClient(
        api_key="public-key",
        api_secret="kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==",
        otp="654321",
        nonce_factory=lambda: 1616492376594,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.kraken.com",
        ),
    )

    balances = client.get_balances()
    open_orders = client.get_open_orders()
    submission = client.add_market_order(pair="XBTUSD", side="buy", volume=0.1)
    queried = client.query_orders(["OID456"])
    cancelled = client.cancel_order("OID123")
    dead_man_switch = client.cancel_all_orders_after(60)

    assert balances["ZUSD"] == 1000.5
    assert open_orders["OID123"].remaining_volume == 0.4
    assert submission.txid == "OID456"
    assert queried["OID456"].average_price == 46000.0
    assert cancelled == 1
    assert dead_man_switch["trigger_time"] == "2026-03-08T12:01:00Z"

    for path, payload, headers in seen_requests:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        assert normalized_headers["api-key"] == "public-key"
        assert "api-sign" in normalized_headers
        assert payload["nonce"] == "1616492376594"
        assert payload["otp"] == "654321"
        assert path.startswith("/0/private/")


def test_private_endpoint_requires_credentials() -> None:
    client = KrakenClient()

    try:
        client.get_balances()
    except KrakenClientError as exc:
        assert "required" in str(exc).lower()
    else:
        raise AssertionError("Expected KrakenClientError when credentials are missing")
