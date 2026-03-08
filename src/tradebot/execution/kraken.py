"""Authenticated and public Kraken spot REST client helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from numbers import Real
from typing import Any, Literal, cast

import httpx

from tradebot.execution.models import KrakenOrderState, OrderSubmission, PairMetadata


class KrakenClientError(RuntimeError):
    """Raised when Kraken returns an error payload or malformed response."""


class KrakenClient:
    """Minimal Kraken REST client for the Phase 7 live engine."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        otp: str | None = None,
        client: httpx.Client | None = None,
        nonce_factory: Callable[[], int] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._otp = otp
        self._client = client or httpx.Client(base_url="https://api.kraken.com", timeout=30.0)
        self._nonce_factory = nonce_factory or default_nonce

    def get_system_status(self) -> dict[str, str | None]:
        """Fetch the current exchange trading status."""
        result = self._public_get("/0/public/SystemStatus")
        return {
            "status": _string_or_none(result.get("status")) or "unknown",
            "timestamp": _string_or_none(result.get("timestamp")),
            "message": _string_or_none(result.get("msg")),
        }

    def get_ticker(self, pairs: Iterable[str]) -> dict[str, float]:
        """Fetch last-trade prices for requested Kraken pairs."""
        requested_pairs = sorted(set(pairs))
        if not requested_pairs:
            return {}

        result = self._public_get(
            "/0/public/Ticker",
            params={"pair": ",".join(requested_pairs)},
        )
        prices: dict[str, float] = {}
        requested_by_altname = {pair: pair for pair in requested_pairs}
        for raw_key, payload in result.items():
            if not isinstance(payload, Mapping):
                continue
            altname = _string_or_none(payload.get("altname")) or raw_key
            close_values = payload.get("c")
            if not isinstance(close_values, list) or not close_values:
                continue
            prices[requested_by_altname.get(altname, altname)] = float(close_values[0])
        return prices

    def get_asset_pairs(self, pairs: Iterable[str]) -> dict[str, PairMetadata]:
        """Fetch pair precision and minimum-order metadata for requested pairs."""
        requested_pairs = sorted(set(pairs))
        if not requested_pairs:
            return {}

        result = self._public_get(
            "/0/public/AssetPairs",
            params={"pair": ",".join(requested_pairs)},
        )
        metadata: dict[str, PairMetadata] = {}
        for raw_key, payload in result.items():
            if not isinstance(payload, Mapping):
                continue
            altname = _string_or_none(payload.get("altname")) or raw_key
            metadata[altname] = PairMetadata(
                pair=raw_key,
                altname=altname,
                wsname=_string_or_none(payload.get("wsname")),
                status=_string_or_none(payload.get("status")),
                lot_decimals=_int_or_default(payload.get("lot_decimals"), default=8),
                ordermin=_float_or_none(payload.get("ordermin")),
                costmin=_float_or_none(payload.get("costmin")),
            )
        return metadata

    def get_balances(self) -> dict[str, float]:
        """Fetch current account balances."""
        result = self._private_post("/0/private/Balance", {})
        return {
            asset: float(value)
            for asset, value in result.items()
            if isinstance(asset, str)
        }

    def get_open_orders(self) -> dict[str, KrakenOrderState]:
        """Fetch currently open orders indexed by Kraken txid."""
        result = self._private_post("/0/private/OpenOrders", {})
        open_orders = result.get("open", {})
        if not isinstance(open_orders, Mapping):
            return {}
        return {
            txid: self._parse_order_state(txid, cast(Mapping[str, Any], payload))
            for txid, payload in open_orders.items()
            if isinstance(txid, str) and isinstance(payload, Mapping)
        }

    def query_orders(self, txids: Iterable[str]) -> dict[str, KrakenOrderState]:
        """Fetch the latest known states for specific orders."""
        requested_txids = sorted(set(txids))
        if not requested_txids:
            return {}
        result = self._private_post(
            "/0/private/QueryOrders",
            {"txid": ",".join(requested_txids)},
        )
        return {
            txid: self._parse_order_state(txid, cast(Mapping[str, Any], payload))
            for txid, payload in result.items()
            if isinstance(txid, str) and isinstance(payload, Mapping)
        }

    def add_market_order(
        self,
        *,
        pair: str,
        side: Literal["buy", "sell"],
        volume: float,
        validate: bool = False,
        userref: int | None = None,
    ) -> OrderSubmission:
        """Place a market order on Kraken."""
        payload: dict[str, Any] = {
            "ordertype": "market",
            "pair": pair,
            "type": side,
            "volume": _format_decimal(volume),
            "validate": str(validate).lower(),
        }
        if userref is not None:
            payload["userref"] = userref
        result = self._private_post("/0/private/AddOrder", payload)
        txids = result.get("txid")
        if not isinstance(txids, list) or not txids:
            raise KrakenClientError(f"Kraken add-order response missing txid: {result!r}")
        description = result.get("descr", {})
        order_description = None
        if isinstance(description, Mapping):
            order_description = _string_or_none(description.get("order"))
        return OrderSubmission(txid=str(txids[0]), description=order_description)

    def cancel_order(self, txid: str) -> int:
        """Cancel a specific open order by Kraken txid."""
        result = self._private_post("/0/private/CancelOrder", {"txid": txid})
        return _int_or_default(result.get("count"), default=0)

    def cancel_all_orders_after(self, timeout_seconds: int) -> dict[str, str | None]:
        """Refresh Kraken's dead-man switch timeout."""
        result = self._private_post(
            "/0/private/CancelAllOrdersAfter",
            {"timeout": timeout_seconds},
        )
        return {
            "current_time": _string_or_none(result.get("currentTime")),
            "trigger_time": _string_or_none(result.get("triggerTime")),
        }

    def _public_get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return self._parse_payload(response.json())

    def _private_post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self._api_key or not self._api_secret:
            raise KrakenClientError("Kraken private API key and secret are required")

        nonce = str(self._nonce_factory())
        encoded_payload: dict[str, Any] = {"nonce": nonce, **dict(payload)}
        if self._otp:
            encoded_payload.setdefault("otp", self._otp)
        signature = kraken_signature(path, encoded_payload, self._api_secret)
        response = self._client.post(
            path,
            data=encoded_payload,
            headers={"API-Key": self._api_key, "API-Sign": signature},
        )
        response.raise_for_status()
        return self._parse_payload(response.json())

    @staticmethod
    def _parse_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise KrakenClientError(f"Kraken response payload is not a mapping: {payload!r}")
        errors = payload.get("error")
        if isinstance(errors, list) and errors:
            raise KrakenClientError(", ".join(str(error) for error in errors))
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise KrakenClientError(f"Kraken response missing mapping result: {payload!r}")
        return dict(result)

    @staticmethod
    def _parse_order_state(txid: str, payload: Mapping[str, Any]) -> KrakenOrderState:
        description = payload.get("descr", {})
        description_mapping = description if isinstance(description, Mapping) else {}
        pair = _string_or_none(description_mapping.get("pair")) or ""
        side = _string_or_none(description_mapping.get("type")) or "buy"
        order_type = _string_or_none(description_mapping.get("ordertype")) or "market"
        requested_volume = _float_or_none(payload.get("vol")) or 0.0
        executed_volume = _float_or_none(payload.get("vol_exec")) or 0.0
        remaining_volume = max(requested_volume - executed_volume, 0.0)
        return KrakenOrderState(
            txid=txid,
            pair=pair,
            side=cast(Literal["buy", "sell"], side if side in {"buy", "sell"} else "buy"),
            order_type=order_type,
            status=_string_or_none(payload.get("status")) or "unknown",
            requested_volume=requested_volume,
            executed_volume=executed_volume,
            remaining_volume=remaining_volume,
            average_price=_float_or_none(payload.get("price")),
            cost_usd=_float_or_none(payload.get("cost")),
            fee_usd=_float_or_none(payload.get("fee")),
            opened_at=_float_or_none(payload.get("opentm")),
            closed_at=_float_or_none(payload.get("closetm")),
            limit_price=_float_or_none(description_mapping.get("price")),
            userref=_int_or_none(payload.get("userref")),
        )


def kraken_signature(url_path: str, payload: Mapping[str, Any], secret: str) -> str:
    """Return the Kraken API-Sign header value for one private REST request."""
    encoded_payload = urllib.parse.urlencode(payload)
    message = (str(payload["nonce"]) + encoded_payload).encode("utf-8")
    digest = hashlib.sha256(message).digest()
    mac = hmac.new(
        base64.b64decode(secret),
        url_path.encode("utf-8") + digest,
        hashlib.sha512,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def default_nonce() -> int:
    """Return a millisecond nonce suitable for Kraken private REST requests."""
    return int(time.time() * 1000)


def _string_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Real):
        return float(value)
    return float(str(value))


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, Real):
        return int(float(value))
    return int(str(value))


def _int_or_default(value: object, *, default: int) -> int:
    parsed = _int_or_none(value)
    return default if parsed is None else parsed


def _format_decimal(value: float) -> str:
    return format(value, "f")
