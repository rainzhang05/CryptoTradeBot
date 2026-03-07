"""Phase 2 data import and integrity orchestration."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from spotbot.config import AppConfig
from spotbot.data.aggregation import CandleAccumulator
from spotbot.data.integrity import check_candles
from spotbot.data.models import (
    AssetImportResult,
    Candle,
    ImportSummary,
    IntegritySummary,
    Interval,
    RawTrade,
    SourceState,
    path_to_string,
)
from spotbot.data.storage import canonical_asset_dir, canonical_candle_file, manifest_file, write_candles, write_json
from spotbot.data.symbols import ASSET_SYMBOLS


class DataService:
    """Service for converting raw Kraken trades into canonical datasets."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.data_settings = config.resolved_data_settings()

    def import_kraken_raw(self, assets: tuple[str, ...] | None = None) -> ImportSummary:
        """Import raw Kraken trades for the selected assets into canonical candles."""
        selected_assets = assets or tuple(ASSET_SYMBOLS)
        results: list[AssetImportResult] = []

        for asset in selected_assets:
            symbol_map = ASSET_SYMBOLS[asset]
            raw_path = self.data_settings.raw_kraken_dir / symbol_map.kraken_raw_file
            if not raw_path.exists():
                results.append(
                    AssetImportResult(
                        asset=asset,
                        pair=symbol_map.kraken_pair,
                        raw_file=None,
                        raw_trade_count=0,
                        malformed_rows=0,
                        out_of_order_rows=0,
                        first_trade_timestamp=None,
                        last_trade_timestamp=None,
                        candles_written={interval: 0 for interval in self.data_settings.intervals},
                        canonical_files={
                            interval: str(canonical_candle_file(self.data_settings.canonical_dir, asset, interval))
                            for interval in self.data_settings.intervals
                        },
                        manifest_file=None,
                    )
                )
                continue

            result = self._import_single_asset(asset=asset, raw_path=raw_path)
            results.append(result)

        report_path = self.data_settings.reports_dir / "latest_import_summary.json"
        write_json(report_path, {"assets": [result.to_dict() for result in results]})
        return ImportSummary(assets=results, report_file=str(report_path))

    def check_canonical(self, assets: tuple[str, ...] | None = None) -> IntegritySummary:
        """Check canonical candles for integrity issues."""
        selected_assets = assets or tuple(ASSET_SYMBOLS)
        results = []

        for asset in selected_assets:
            for interval in self.data_settings.intervals:
                candle_path = canonical_candle_file(self.data_settings.canonical_dir, asset, interval)
                if candle_path.exists():
                    results.append(check_candles(asset=asset, interval=interval, path=candle_path))

        report_path = self.data_settings.reports_dir / "latest_integrity_report.json"
        write_json(report_path, {"results": [result.to_dict() for result in results]})
        return IntegritySummary(results=results, report_file=str(report_path))

    def source_summary(self) -> dict[str, object]:
        """Return raw and canonical source coverage for the fixed-universe assets."""
        states: list[SourceState] = []
        for asset, symbol_map in ASSET_SYMBOLS.items():
            raw_path = self.data_settings.raw_kraken_dir / symbol_map.kraken_raw_file
            states.append(
                SourceState(
                    asset=asset,
                    pair=symbol_map.kraken_pair,
                    raw_file=path_to_string(raw_path if raw_path.exists() else None),
                    canonical_files={
                        interval: path_to_string(path)
                        if path.exists()
                        else None
                        for interval in self.data_settings.intervals
                        for path in [canonical_candle_file(self.data_settings.canonical_dir, asset, interval)]
                    },
                )
            )

        return {"assets": [state.to_dict() for state in states]}

    def _import_single_asset(self, asset: str, raw_path: Path) -> AssetImportResult:
        symbol_map = ASSET_SYMBOLS[asset]
        aggregators = {
            interval: CandleAccumulator(interval=interval, source="kraken_raw")
            for interval in self.data_settings.intervals
        }
        candles_by_interval: dict[Interval, list[Candle]] = {
            interval: [] for interval in self.data_settings.intervals
        }

        malformed_rows = 0
        out_of_order_rows = 0
        raw_trade_count = 0
        first_trade_timestamp: int | None = None
        last_trade_timestamp: int | None = None

        with raw_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue

                try:
                    trade = self._parse_trade_line(stripped)
                except ValueError:
                    malformed_rows += 1
                    continue

                raw_trade_count += 1
                if first_trade_timestamp is None:
                    first_trade_timestamp = trade.timestamp
                last_trade_timestamp = trade.timestamp

                for interval, aggregator in aggregators.items():
                    try:
                        emitted = aggregator.add_trade(trade)
                    except ValueError:
                        out_of_order_rows += 1
                        emitted = None

                    if emitted is not None:
                        candles_by_interval[interval].append(emitted)

        for interval, aggregator in aggregators.items():
            final_candle = aggregator.finish()
            if final_candle is not None:
                candles_by_interval[interval].append(final_candle)

        canonical_files: dict[Interval, str] = {}
        candles_written: dict[Interval, int] = {}
        for interval, candles in candles_by_interval.items():
            candle_path = canonical_candle_file(self.data_settings.canonical_dir, asset, interval)
            candles_written[interval] = write_candles(candle_path, candles)
            canonical_files[interval] = str(candle_path)

        manifest_path = manifest_file(self.data_settings.canonical_dir, asset)
        write_json(
            manifest_path,
            {
                "asset": asset,
                "pair": symbol_map.kraken_pair,
                "raw_file": str(raw_path),
                "generated_at": datetime.now(tz=UTC).isoformat(),
                "raw_trade_count": raw_trade_count,
                "malformed_rows": malformed_rows,
                "out_of_order_rows": out_of_order_rows,
                "first_trade_timestamp": first_trade_timestamp,
                "last_trade_timestamp": last_trade_timestamp,
                "candles_written": candles_written,
                "canonical_files": canonical_files,
            },
        )

        return AssetImportResult(
            asset=asset,
            pair=symbol_map.kraken_pair,
            raw_file=str(raw_path),
            raw_trade_count=raw_trade_count,
            malformed_rows=malformed_rows,
            out_of_order_rows=out_of_order_rows,
            first_trade_timestamp=first_trade_timestamp,
            last_trade_timestamp=last_trade_timestamp,
            candles_written=candles_written,
            canonical_files=canonical_files,
            manifest_file=str(manifest_path),
        )

    @staticmethod
    def _parse_trade_line(line: str) -> RawTrade:
        parts = line.split(",")
        if len(parts) != 3:
            raise ValueError("expected exactly three CSV fields")
        timestamp_str, price_str, volume_str = parts
        return RawTrade(
            timestamp=int(float(timestamp_str)),
            price=float(price_str),
            volume=float(volume_str),
        )