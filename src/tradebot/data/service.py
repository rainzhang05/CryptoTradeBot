"""Phase 2 data import and integrity orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from tradebot.cancellation import CancellationToken
from tradebot.config import AppConfig
from tradebot.data.aggregation import CandleAccumulator
from tradebot.data.clients import (
    BinancePublicClient,
    CoinbasePublicClient,
    DataClientError,
    KrakenPublicClient,
)
from tradebot.data.integrity import check_candles, read_candles
from tradebot.data.models import (
    INTERVAL_SECONDS,
    AssetImportResult,
    Candle,
    ImportSummary,
    IntegritySummary,
    Interval,
    RawTrade,
    SourceState,
    path_to_string,
)
from tradebot.data.storage import (
    canonical_candle_file,
    manifest_file,
    write_candles,
    write_json,
)
from tradebot.data.symbols import ASSET_SYMBOLS, AssetSymbolMap
from tradebot.logging_config import get_logger


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
        self.logger = get_logger("tradebot.data.service")

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

    def prune_raw_kraken(self) -> dict[str, object]:
        """Remove raw Kraken files that are not part of the fixed V1 universe."""
        keep_files = {symbol.kraken_raw_file for symbol in ASSET_SYMBOLS.values()}
        deleted: list[str] = []

        for path in sorted(self.data_settings.raw_kraken_dir.glob("*.csv")):
            if path.name not in keep_files:
                path.unlink()
                deleted.append(str(path))

        return {
            "deleted_count": len(deleted),
            "kept_files": sorted(keep_files),
            "deleted_files": deleted[:50],
            "deleted_files_truncated": len(deleted) > 50,
        }

    def sync_canonical(self, assets: tuple[str, ...] | None = None) -> dict[str, object]:
        """Extend canonical candles using Kraken and fallback public sources."""
        selected_assets = assets or tuple(ASSET_SYMBOLS)
        synced_assets: list[dict[str, object]] = []
        for asset in selected_assets:
            synced_assets.append(self._sync_asset(asset))

        report_path = self.data_settings.reports_dir / "latest_sync_summary.json"
        write_json(report_path, {"assets": synced_assets})
        return {"assets": synced_assets, "report_file": str(report_path)}

    def complete_canonical(
        self,
        assets: tuple[str, ...] | None = None,
        allow_synthetic: bool = True,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        """Repair historical gaps and extend canonical candles to the latest closed interval."""
        selected_assets = assets or tuple(ASSET_SYMBOLS)
        completion_assets: list[dict[str, object]] = []
        self.logger.info(
            "starting canonical completion",
            extra={
                "asset_count": len(selected_assets),
                "allow_synthetic": allow_synthetic,
            },
        )

        for asset in selected_assets:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            self.logger.info("processing completion asset", extra={"asset": asset})
            if any(
                not canonical_candle_file(
                    self.data_settings.canonical_dir,
                    asset,
                    interval,
                ).exists()
                for interval in self.data_settings.intervals
            ):
                self.import_kraken_raw(assets=(asset,))

            interval_results: list[dict[str, object]] = []
            for interval in self.data_settings.intervals:
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                candle_path = canonical_candle_file(
                    self.data_settings.canonical_dir,
                    asset,
                    interval,
                )
                self.logger.info(
                    "processing completion interval",
                    extra={"asset": asset, "interval": interval, "path": str(candle_path)},
                )
                if not candle_path.exists():
                    interval_results.append({"interval": interval, "status": "missing_canonical"})
                    continue

                existing = read_candles(candle_path)
                if not existing:
                    interval_results.append({"interval": interval, "status": "empty_canonical"})
                    continue

                before = check_candles(asset=asset, interval=interval, path=candle_path)
                target_end = self._latest_closed_timestamp(interval)
                self.logger.info(
                    "completion interval state",
                    extra={
                        "asset": asset,
                        "interval": interval,
                        "missing_before": before.missing_intervals,
                        "last_timestamp": before.last_timestamp,
                        "target_end": target_end,
                    },
                )
                completed, stats = self._complete_interval(
                    asset=asset,
                    interval=interval,
                    candles=existing,
                    target_end=target_end,
                    allow_synthetic=allow_synthetic,
                    cancellation_token=cancellation_token,
                    progress_callback=progress_callback,
                )
                write_candles(candle_path, completed)
                after = check_candles(asset=asset, interval=interval, path=candle_path)
                self.logger.info(
                    "completion interval finished",
                    extra={
                        "asset": asset,
                        "interval": interval,
                        "missing_after": after.missing_intervals,
                        "remaining_gap_ranges": stats["remaining_gap_ranges"],
                        "kraken_api_added": stats["kraken_api_added"],
                        "kraken_native_replaced": stats["kraken_native_replaced"],
                        "binance_added": stats["binance_added"],
                        "coinbase_added": stats["coinbase_added"],
                        "synthetic_added": stats["synthetic_added"],
                    },
                )
                interval_results.append(
                    {
                        "interval": interval,
                        "status": "continuous" if after.missing_intervals == 0 else "incomplete",
                        "previous_last_timestamp": before.last_timestamp,
                        "current_last_timestamp": after.last_timestamp,
                        "missing_intervals_before": before.missing_intervals,
                        "missing_intervals_after": after.missing_intervals,
                        "candles_before": before.candle_count,
                        "candles_after": after.candle_count,
                        **stats,
                    }
                )
                if progress_callback is not None:
                    progress_callback(
                        {
                            "asset": asset,
                            "interval": interval,
                            "status": interval_results[-1]["status"],
                            "completed_ranges": stats["total_ranges_completed"],
                            "total_ranges_planned": stats["total_ranges_planned"],
                        }
                    )

            completion_assets.append({"asset": asset, "intervals": interval_results})

        report_path = self.data_settings.reports_dir / "latest_completion_summary.json"
        payload = {"assets": completion_assets, "allow_synthetic": allow_synthetic}
        write_json(report_path, payload)
        self.logger.info("canonical completion finished", extra={"report_file": str(report_path)})
        return {"assets": completion_assets, "report_file": str(report_path)}

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
            target_end = self._latest_closed_timestamp(interval)
            start_timestamp = last_timestamp + step
            if start_timestamp > target_end:
                intervals.append({"interval": interval, "status": "up_to_date", "appended": 0})
                continue

            kraken_rows = self._fetch_kraken_range(
                asset=asset,
                pair=symbol_map.kraken_raw_file.removesuffix(".csv"),
                interval=interval,
                start_ts=start_timestamp,
                end_ts=target_end,
            )

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

    def _complete_interval(
        self,
        *,
        asset: str,
        interval: Interval,
        candles: list[Candle],
        target_end: int,
        allow_synthetic: bool,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[list[Candle], dict[str, Any]]:
        symbol_map = ASSET_SYMBOLS[asset]
        merged = candles
        source_counts = {
            "kraken_api_added": 0,
            "kraken_native_replaced": 0,
            "binance_added": 0,
            "coinbase_added": 0,
            "synthetic_added": 0,
        }

        native_refresh_ranges = self._non_kraken_ranges(merged, interval, target_end=target_end)
        missing_ranges = self._missing_ranges(merged, interval, target_end=target_end)
        total_ranges_planned = len(native_refresh_ranges) + len(missing_ranges)
        completed_ranges = 0
        started_at = monotonic()

        self.logger.info(
            "completion interval workload",
            extra={
                "asset": asset,
                "interval": interval,
                "native_refresh_ranges": len(native_refresh_ranges),
                "missing_ranges": len(missing_ranges),
                "total_ranges_planned": total_ranges_planned,
            },
        )

        for start_ts, end_ts in native_refresh_ranges:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            refreshed_rows = self._fetch_kraken_range(
                asset=asset,
                pair=symbol_map.kraken_raw_file.removesuffix(".csv"),
                interval=interval,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            merged, _, replaced = self._merge_candles_with_stats(merged, refreshed_rows)
            source_counts["kraken_native_replaced"] += replaced
            completed_ranges += 1
            self._log_completion_progress(
                asset=asset,
                interval=interval,
                completed_ranges=completed_ranges,
                total_ranges_planned=total_ranges_planned,
                started_at=started_at,
                phase="native_refresh",
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "asset": asset,
                        "interval": interval,
                        "phase": "native_refresh",
                        "completed_ranges": completed_ranges,
                        "total_ranges_planned": total_ranges_planned,
                    }
                )

        for start_ts, end_ts in missing_ranges:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            if start_ts > end_ts:
                continue

            kraken_rows = self._fetch_kraken_range(
                asset=asset,
                pair=symbol_map.kraken_raw_file.removesuffix(".csv"),
                interval=interval,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            merged, added, replaced = self._merge_candles_with_stats(merged, kraken_rows)
            source_counts["kraken_api_added"] += added
            source_counts["kraken_native_replaced"] += replaced

            unresolved = self._missing_ranges_in_window(merged, interval, start_ts, end_ts)
            for unresolved_start, unresolved_end in unresolved:
                binance_rows = self._safe_fetch_binance(
                    symbol_map,
                    interval,
                    unresolved_start,
                    unresolved_end,
                )
                merged, added, _ = self._merge_candles_with_stats(merged, binance_rows)
                source_counts["binance_added"] += added

                coinbase_gaps = self._missing_ranges_in_window(
                    merged,
                    interval,
                    unresolved_start,
                    unresolved_end,
                )
                for coinbase_start, coinbase_end in coinbase_gaps:
                    coinbase_rows = self._safe_fetch_coinbase(
                        symbol_map,
                        interval,
                        coinbase_start,
                        coinbase_end,
                    )
                    merged, added, _ = self._merge_candles_with_stats(merged, coinbase_rows)
                    source_counts["coinbase_added"] += added

            if allow_synthetic:
                synthetic_gaps = self._missing_ranges_in_window(merged, interval, start_ts, end_ts)
                merged, added, _ = self._merge_candles_with_stats(
                    merged,
                    self._synthesize_gap_fill(merged, interval, synthetic_gaps),
                )
                source_counts["synthetic_added"] += added

            completed_ranges += 1
            self._log_completion_progress(
                asset=asset,
                interval=interval,
                completed_ranges=completed_ranges,
                total_ranges_planned=total_ranges_planned,
                started_at=started_at,
                phase="gap_fill",
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "asset": asset,
                        "interval": interval,
                        "phase": "gap_fill",
                        "completed_ranges": completed_ranges,
                        "total_ranges_planned": total_ranges_planned,
                    }
                )

        remaining_ranges = self._missing_ranges(merged, interval, target_end=target_end)
        return merged, {
            **source_counts,
            "total_ranges_planned": total_ranges_planned,
            "total_ranges_completed": completed_ranges,
            "remaining_gap_ranges": len(remaining_ranges),
            "target_end_timestamp": target_end,
            "target_end_iso": datetime.fromtimestamp(target_end, tz=UTC).isoformat(),
            "elapsed_seconds": round(monotonic() - started_at, 2),
            "eta_seconds": 0.0,
        }

    def _log_completion_progress(
        self,
        *,
        asset: str,
        interval: Interval,
        completed_ranges: int,
        total_ranges_planned: int,
        started_at: float,
        phase: str,
    ) -> None:
        if total_ranges_planned == 0:
            return
        if completed_ranges not in {1, total_ranges_planned} and completed_ranges % 100 != 0:
            return

        elapsed_seconds = monotonic() - started_at
        eta_seconds = self._estimate_eta_seconds(
            elapsed_seconds=elapsed_seconds,
            completed_ranges=completed_ranges,
            total_ranges_planned=total_ranges_planned,
        )
        self.logger.info(
            "completion interval progress",
            extra={
                "asset": asset,
                "interval": interval,
                "phase": phase,
                "completed_ranges": completed_ranges,
                "total_ranges_planned": total_ranges_planned,
                "remaining_ranges": total_ranges_planned - completed_ranges,
                "elapsed_seconds": round(elapsed_seconds, 2),
                "eta_seconds": None if eta_seconds is None else round(eta_seconds, 2),
            },
        )

    @staticmethod
    def _estimate_eta_seconds(
        *,
        elapsed_seconds: float,
        completed_ranges: int,
        total_ranges_planned: int,
    ) -> float | None:
        if completed_ranges <= 0 or total_ranges_planned <= completed_ranges:
            return 0.0 if total_ranges_planned == completed_ranges else None

        seconds_per_range = elapsed_seconds / completed_ranges
        remaining_ranges = total_ranges_planned - completed_ranges
        return seconds_per_range * remaining_ranges

    def _fetch_fallback(
        self,
        symbol_map: AssetSymbolMap,
        interval: Interval,
        start_timestamp: int,
        end_timestamp: int,
    ) -> tuple[list[Candle], str | None]:
        if start_timestamp > end_timestamp:
            return [], None

        binance_rows = self._safe_fetch_binance(
            symbol_map,
            interval,
            start_timestamp,
            end_timestamp,
        )
        if binance_rows:
            return binance_rows, "binance"

        coinbase_rows = self._safe_fetch_coinbase(
            symbol_map,
            interval,
            start_timestamp,
            end_timestamp,
        )
        return coinbase_rows, "coinbase" if coinbase_rows else None

    def _fetch_kraken_range(
        self,
        *,
        asset: str,
        pair: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        try:
            return self.kraken_client.fetch_ohlc_range(
                pair=pair,
                interval=interval,
                start_ts=start_ts,
                end_ts=end_ts,
            )
        except (DataClientError, httpx.HTTPError) as exc:
            self.logger.warning(
                "kraken fetch failed, falling back",
                extra={
                    "asset": asset,
                    "pair": pair,
                    "interval": interval,
                    "start_timestamp": start_ts,
                    "end_timestamp": end_ts,
                    "error": str(exc),
                },
            )
            return []

    def _fetch_binance(
        self,
        symbol_map: AssetSymbolMap,
        interval: Interval,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[Candle]:
        if start_timestamp > end_timestamp:
            return []
        asset_symbols = ASSET_SYMBOLS[symbol_map.asset]
        self.logger.info(
            "requesting binance fallback",
            extra={
                "asset": symbol_map.asset,
                "interval": interval,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
        )
        return self.binance_client.fetch_klines(
            symbol=asset_symbols.binance_symbol,
            interval=interval,
            start_ts=start_timestamp,
            end_ts=end_timestamp,
        )

    def _safe_fetch_binance(
        self,
        symbol_map: AssetSymbolMap,
        interval: Interval,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[Candle]:
        try:
            return self._fetch_binance(symbol_map, interval, start_timestamp, end_timestamp)
        except Exception as exc:
            self.logger.warning(
                "binance fallback failed",
                extra={
                    "asset": symbol_map.asset,
                    "interval": interval,
                    "start_timestamp": start_timestamp,
                    "end_timestamp": end_timestamp,
                    "error": str(exc),
                },
            )
            return []

    def _fetch_coinbase(
        self,
        symbol_map: AssetSymbolMap,
        interval: Interval,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[Candle]:
        if start_timestamp > end_timestamp:
            return []
        asset_symbols = ASSET_SYMBOLS[symbol_map.asset]
        self.logger.info(
            "requesting coinbase fallback",
            extra={
                "asset": symbol_map.asset,
                "interval": interval,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
        )
        return self.coinbase_client.fetch_candles(
            product_id=asset_symbols.coinbase_product,
            interval=interval,
            start_ts=start_timestamp,
            end_ts=end_timestamp,
        )

    def _safe_fetch_coinbase(
        self,
        symbol_map: AssetSymbolMap,
        interval: Interval,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[Candle]:
        try:
            return self._fetch_coinbase(symbol_map, interval, start_timestamp, end_timestamp)
        except Exception as exc:
            self.logger.warning(
                "coinbase fallback failed",
                extra={
                    "asset": symbol_map.asset,
                    "interval": interval,
                    "start_timestamp": start_timestamp,
                    "end_timestamp": end_timestamp,
                    "error": str(exc),
                },
            )
            return []

    @staticmethod
    def _merge_candles(existing: list[Candle], incoming: list[Candle]) -> list[Candle]:
        merged, _, _ = DataService._merge_candles_with_stats(existing, incoming)
        return merged

    @staticmethod
    def _merge_candles_with_stats(
        existing: list[Candle], incoming: list[Candle]
    ) -> tuple[list[Candle], int, int]:
        merged: dict[int, Candle] = {candle.timestamp: candle for candle in existing}
        added = 0
        replaced = 0
        for candle in incoming:
            current = merged.get(candle.timestamp)
            if current is None:
                merged[candle.timestamp] = candle
                added += 1
                continue

            if DataService._source_priority(candle.source) > DataService._source_priority(
                current.source,
            ):
                merged[candle.timestamp] = candle
                replaced += 1
        return [merged[timestamp] for timestamp in sorted(merged)], added, replaced

    @staticmethod
    def _source_priority(source: str) -> int:
        priorities = {
            "synthetic_gap_fill": 0,
            "coinbase": 1,
            "coinbase_fallback": 1,
            "binance": 2,
            "binance_fallback": 2,
            "kraken": 3,
            "kraken_api": 3,
            "kraken_raw": 4,
        }
        return priorities.get(source, 0)

    @staticmethod
    def _missing_ranges(
        candles: list[Candle],
        interval: Interval,
        *,
        target_end: int | None = None,
    ) -> list[tuple[int, int]]:
        if not candles:
            return []

        step = INTERVAL_SECONDS[interval]
        ranges: list[tuple[int, int]] = []
        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        previous_timestamp = ordered[0].timestamp
        for candle in ordered[1:]:
            if candle.timestamp > previous_timestamp + step:
                ranges.append((previous_timestamp + step, candle.timestamp - step))
            previous_timestamp = candle.timestamp

        if target_end is not None and previous_timestamp < target_end:
            ranges.append((previous_timestamp + step, target_end))
        return ranges

    @staticmethod
    def _non_kraken_ranges(
        candles: list[Candle],
        interval: Interval,
        *,
        target_end: int | None = None,
    ) -> list[tuple[int, int]]:
        step = INTERVAL_SECONDS[interval]
        kraken_sources = {"kraken_raw", "kraken_api"}
        candidate_timestamps = sorted(
            candle.timestamp
            for candle in candles
            if candle.source not in kraken_sources
            and (target_end is None or candle.timestamp <= target_end)
        )
        if not candidate_timestamps:
            return []

        ranges: list[tuple[int, int]] = []
        start_ts = candidate_timestamps[0]
        previous_ts = candidate_timestamps[0]
        for timestamp in candidate_timestamps[1:]:
            if timestamp == previous_ts + step:
                previous_ts = timestamp
                continue
            ranges.append((start_ts, previous_ts))
            start_ts = timestamp
            previous_ts = timestamp

        ranges.append((start_ts, previous_ts))
        return ranges

    @staticmethod
    def _missing_ranges_in_window(
        candles: list[Candle],
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[tuple[int, int]]:
        if start_ts > end_ts:
            return []

        step = INTERVAL_SECONDS[interval]
        timestamps = sorted(
            candle.timestamp for candle in candles if start_ts <= candle.timestamp <= end_ts
        )
        ranges: list[tuple[int, int]] = []
        expected = start_ts

        for timestamp in timestamps:
            if timestamp > expected:
                ranges.append((expected, timestamp - step))
            expected = timestamp + step

        if expected <= end_ts:
            ranges.append((expected, end_ts))
        return ranges

    @staticmethod
    def _synthesize_gap_fill(
        candles: list[Candle],
        interval: Interval,
        gap_ranges: list[tuple[int, int]],
    ) -> list[Candle]:
        if not gap_ranges:
            return []

        step = INTERVAL_SECONDS[interval]
        by_timestamp = {candle.timestamp: candle for candle in candles}
        synthetic: list[Candle] = []
        for start_ts, end_ts in gap_ranges:
            previous = by_timestamp.get(start_ts - step)
            if previous is None:
                continue
            timestamp = start_ts
            while timestamp <= end_ts:
                synthetic_candle = Candle(
                    timestamp=timestamp,
                    open=previous.close,
                    high=previous.close,
                    low=previous.close,
                    close=previous.close,
                    volume=0.0,
                    trade_count=1,
                    source="synthetic_gap_fill",
                )
                synthetic.append(synthetic_candle)
                previous = synthetic_candle
                timestamp += step
        return synthetic

    @staticmethod
    def _latest_closed_timestamp(interval: Interval) -> int:
        step = INTERVAL_SECONDS[interval]
        return (int(datetime.now(tz=UTC).timestamp()) // step) * step - step

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
