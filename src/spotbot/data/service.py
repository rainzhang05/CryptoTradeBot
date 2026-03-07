"""Phase 2 data import and integrity orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from spotbot.config import AppConfig
from spotbot.data.aggregation import CandleAccumulator
from spotbot.data.clients import BinancePublicClient, CoinbasePublicClient, KrakenPublicClient
from spotbot.data.integrity import check_candles, read_candles
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
from spotbot.data.storage import (
    canonical_candle_file,
    manifest_file,
    write_candles,
    write_json,
)
from spotbot.data.symbols import ASSET_SYMBOLS, AssetSymbolMap


class DataService:
    """Service for converting raw Kraken trades into canonical datasets."""

    def __init__(
        self,
        config: AppConfig,
        kraken_client: KrakenPublicClient | None = None,
        binance_client: BinancePublicClient | None = None,
        coinbase_client: CoinbasePublicClient | None = None,
    ) -> None:
        self.config = config
        self.data_settings = config.resolved_data_settings()
        self.kraken_client = kraken_client or KrakenPublicClient()
        self.binance_client = binance_client or BinancePublicClient()
        self.coinbase_client = coinbase_client or CoinbasePublicClient()

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
                            interval: str(
                                canonical_candle_file(
                                    self.data_settings.canonical_dir, asset, interval
                                )
                            )
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
                candle_path = canonical_candle_file(
                    self.data_settings.canonical_dir, asset, interval
                )
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
                        interval: path_to_string(path) if path.exists() else None
                        for interval in self.data_settings.intervals
                        for path in [
                            canonical_candle_file(self.data_settings.canonical_dir, asset, interval)
                        ]
                    },
                )
            )

        return {"assets": [state.to_dict() for state in states]}

    def sync_canonical(self, assets: tuple[str, ...] | None = None) -> dict[str, object]:
        """Extend canonical candles using Kraken and fallback public sources."""
        selected_assets = assets or tuple(ASSET_SYMBOLS)
        synced_assets: list[dict[str, object]] = []
        for asset in selected_assets:
            synced_assets.append(self._sync_asset(asset))

        report_path = self.data_settings.reports_dir / "latest_sync_summary.json"
        write_json(report_path, {"assets": synced_assets})
        return {"assets": synced_assets, "report_file": str(report_path)}

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

    def _sync_asset(self, asset: str) -> dict[str, object]:
        symbol_map = ASSET_SYMBOLS[asset]
        intervals: list[dict[str, object]] = []

        for interval in self.data_settings.intervals:
            candle_path = canonical_candle_file(self.data_settings.canonical_dir, asset, interval)
            if not candle_path.exists():
                intervals.append({"interval": interval, "status": "missing_canonical"})
                continue

            existing = read_candles(candle_path)
            if not existing:
                intervals.append({"interval": interval, "status": "empty_canonical"})
                continue

            step = 3600 if interval == "1h" else 86400
            last_timestamp = existing[-1].timestamp
            target_end = (int(datetime.now(tz=UTC).timestamp()) // step) * step - step
            start_timestamp = last_timestamp + step
            if start_timestamp > target_end:
                intervals.append({"interval": interval, "status": "up_to_date", "appended": 0})
                continue

            kraken_rows = self.kraken_client.fetch_ohlc(
                pair=symbol_map.kraken_raw_file.removesuffix(".csv"),
                interval=interval,
                since=last_timestamp,
            )
            kraken_rows = [candle for candle in kraken_rows if candle.timestamp >= start_timestamp]

            merged_new: list[Candle] = []
            fallback_source = None
            if kraken_rows and kraken_rows[0].timestamp > start_timestamp:
                fallback_end = kraken_rows[0].timestamp - step
                fallback_rows, fallback_source = self._fetch_fallback(
                    symbol_map, interval, start_timestamp, fallback_end
                )
                merged_new.extend(fallback_rows)
            elif not kraken_rows:
                fallback_rows, fallback_source = self._fetch_fallback(
                    symbol_map, interval, start_timestamp, target_end
                )
                merged_new.extend(fallback_rows)

            merged_new.extend(kraken_rows)
            deduped = self._merge_candles(existing, merged_new)
            appended = max(len(deduped) - len(existing), 0)
            write_candles(candle_path, deduped)
            intervals.append(
                {
                    "interval": interval,
                    "status": "synced",
                    "appended": appended,
                    "fallback_source": fallback_source,
                }
            )

        return {"asset": asset, "intervals": intervals}

    def _fetch_fallback(
        self,
        symbol_map: AssetSymbolMap,
        interval: Interval,
        start_timestamp: int,
        end_timestamp: int,
    ) -> tuple[list[Candle], str | None]:
        if start_timestamp > end_timestamp:
            return [], None

        asset_symbols = ASSET_SYMBOLS[symbol_map.asset]
        try:
            rows = self.binance_client.fetch_klines(
                symbol=asset_symbols.binance_symbol,
                interval=interval,
                start_ts=start_timestamp,
                end_ts=end_timestamp,
            )
            return rows, "binance"
        except Exception:
            rows = self.coinbase_client.fetch_candles(
                product_id=asset_symbols.coinbase_product,
                interval=interval,
                start_ts=start_timestamp,
                end_ts=end_timestamp,
            )
            return rows, "coinbase"

    @staticmethod
    def _merge_candles(existing: list[Candle], incoming: list[Candle]) -> list[Candle]:
        merged: dict[int, Candle] = {candle.timestamp: candle for candle in existing}
        for candle in incoming:
            current = merged.get(candle.timestamp)
            if current is None or current.source != "kraken_api":
                merged[candle.timestamp] = candle
        return [merged[timestamp] for timestamp in sorted(merged)]

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