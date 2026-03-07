"""Stable symbol mapping for the fixed V1 universe."""

from __future__ import annotations

from dataclasses import dataclass

from tradebot.constants import FIXED_UNIVERSE


@dataclass(frozen=True)
class AssetSymbolMap:
    """Pair identifiers across supported exchanges for one asset."""

    asset: str
    kraken_pair: str
    kraken_raw_file: str
    binance_symbol: str
    coinbase_product: str


ASSET_SYMBOLS: dict[str, AssetSymbolMap] = {
    "BTC": AssetSymbolMap("BTC", "XBT/USD", "XBTUSD.csv", "BTCUSDT", "BTC-USD"),
    "ETH": AssetSymbolMap("ETH", "ETH/USD", "ETHUSD.csv", "ETHUSDT", "ETH-USD"),
    "BNB": AssetSymbolMap("BNB", "BNB/USD", "BNBUSD.csv", "BNBUSDT", "BNB-USD"),
    "XRP": AssetSymbolMap("XRP", "XRP/USD", "XRPUSD.csv", "XRPUSDT", "XRP-USD"),
    "SOL": AssetSymbolMap("SOL", "SOL/USD", "SOLUSD.csv", "SOLUSDT", "SOL-USD"),
    "ADA": AssetSymbolMap("ADA", "ADA/USD", "ADAUSD.csv", "ADAUSDT", "ADA-USD"),
    "DOGE": AssetSymbolMap("DOGE", "XDG/USD", "XDGUSD.csv", "DOGEUSDT", "DOGE-USD"),
    "TRX": AssetSymbolMap("TRX", "TRX/USD", "TRXUSD.csv", "TRXUSDT", "TRX-USD"),
    "AVAX": AssetSymbolMap("AVAX", "AVAX/USD", "AVAXUSD.csv", "AVAXUSDT", "AVAX-USD"),
    "LINK": AssetSymbolMap("LINK", "LINK/USD", "LINKUSD.csv", "LINKUSDT", "LINK-USD"),
}


def validate_fixed_universe_mapping() -> None:
    """Ensure symbol mapping fully covers the documented V1 universe."""
    if tuple(ASSET_SYMBOLS) != FIXED_UNIVERSE:
        raise ValueError("Asset symbol mapping must exactly match the fixed V1 universe")


validate_fixed_universe_mapping()