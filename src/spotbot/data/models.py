"""Data models for raw trades, canonical candles, and integrity reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

Interval = Literal["1h", "1d"]

INTERVAL_SECONDS: dict[Interval, int] = {
    "1h": 3600,
    "1d": 86400,
}


@dataclass(frozen=True)
class RawTrade:
    """A single raw trade from the Kraken local dump."""

    timestamp: int
    price: float
    volume: float


@dataclass(frozen=True)
class Candle:
    """Canonical candle record derived from raw trades or API candles."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int
    source: str

    def to_row(self) -> dict[str, str]:
        """Serialize a candle into CSV-compatible strings."""
        return {
            "timestamp": str(self.timestamp),
            "open": f"{self.open:.10f}",
            "high": f"{self.high:.10f}",
            "low": f"{self.low:.10f}",
            "close": f"{self.close:.10f}",
            "volume": f"{self.volume:.10f}",
            "trade_count": str(self.trade_count),
            "source": self.source,
        }


@dataclass(frozen=True)
class AssetImportResult:
    """Summary for one asset import run."""

    asset: str
    pair: str
    raw_file: str | None
    raw_trade_count: int
    malformed_rows: int
    out_of_order_rows: int
    first_trade_timestamp: int | None
    last_trade_timestamp: int | None
    candles_written: dict[Interval, int]
    canonical_files: dict[Interval, str]
    manifest_file: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportSummary:
    """Summary of a multi-asset import run."""

    assets: list[AssetImportResult]
    report_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets": [asset.to_dict() for asset in self.assets],
            "report_file": self.report_file,
        }


@dataclass(frozen=True)
class IntegrityResult:
    """Integrity report for one canonical candle file."""

    asset: str
    interval: Interval
    candle_count: int
    first_timestamp: int | None
    last_timestamp: int | None
    duplicate_timestamps: int
    out_of_order_timestamps: int
    missing_intervals: int
    non_positive_rows: int
    file_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntegritySummary:
    """Integrity report bundle for all checked assets."""

    results: list[IntegrityResult]
    report_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [result.to_dict() for result in self.results],
            "report_file": self.report_file,
        }


@dataclass(frozen=True)
class SourceState:
    """Availability state for raw and canonical data for one asset."""

    asset: str
    pair: str
    raw_file: str | None
    canonical_files: dict[Interval, str | None]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def path_to_string(path: Path | None) -> str | None:
    """Convert optional paths to JSON-friendly strings."""
    return None if path is None else str(path)