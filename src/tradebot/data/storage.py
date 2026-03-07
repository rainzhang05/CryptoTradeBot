"""Canonical data storage helpers."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tradebot.data.models import Candle, Interval


def canonical_asset_dir(root: Path, asset: str) -> Path:
    """Return the canonical storage directory for one asset."""
    return root / "kraken" / asset


def canonical_candle_file(root: Path, asset: str, interval: Interval) -> Path:
    """Return the canonical candle file path for an asset interval pair."""
    return canonical_asset_dir(root, asset) / f"candles_{interval}.csv"


def manifest_file(root: Path, asset: str) -> Path:
    """Return the manifest file path for an asset."""
    return canonical_asset_dir(root, asset) / "manifest.json"


def write_candles(path: Path, candles: Iterable[Candle]) -> int:
    """Write canonical candles to CSV and return the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "trade_count",
                "source",
            ],
        )
        writer.writeheader()
        for candle in candles:
            writer.writerow(candle.to_row())
            count += 1
    return count


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON payload to disk in a stable format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def dataclass_json_payload(value: Any) -> dict[str, Any]:
    """Convert a dataclass instance into a JSON-friendly dictionary."""
    payload = asdict(value)
    return payload if isinstance(payload, dict) else {"value": payload}