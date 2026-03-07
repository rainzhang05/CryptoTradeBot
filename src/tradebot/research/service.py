"""Service for building deterministic research datasets from canonical candles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tradebot.config import AppConfig
from tradebot.constants import FIXED_UNIVERSE
from tradebot.data.integrity import read_candles
from tradebot.data.models import Candle
from tradebot.data.storage import canonical_candle_file, write_json
from tradebot.research.features import build_feature_rows, feature_column_names
from tradebot.research.models import AssetDatasetStats, FeatureBuildSummary
from tradebot.research.storage import (
    feature_dataset_file,
    feature_manifest_file,
    write_dataset_rows,
)


class ResearchService:
    """Build reproducible feature and label datasets for the hybrid strategy."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.data_settings = config.resolved_data_settings()

    def build_feature_store(
        self,
        assets: tuple[str, ...] | None = None,
        force: bool = False,
    ) -> FeatureBuildSummary:
        """Build or reuse a deterministic dataset for the selected assets."""
        selected_assets = self._select_assets(assets)
        candles_by_asset = self._load_daily_candles(selected_assets)
        dataset_id = self._dataset_id(selected_assets, candles_by_asset)
        dataset_path = feature_dataset_file(self.paths.features_dir, dataset_id)
        manifest_path = feature_manifest_file(self.paths.features_dir, dataset_id)
        experiment_root = self.paths.experiments_dir / dataset_id

        if not force and dataset_path.exists() and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return self._summary_from_manifest(
                dataset_id=dataset_id,
                dataset_path=dataset_path,
                manifest_path=manifest_path,
                experiment_root=experiment_root,
                manifest=manifest,
                cached=True,
            )

        rows, stats = build_feature_rows(candles_by_asset, self.config.research)
        fieldnames = feature_column_names(self.config.research)
        row_count = write_dataset_rows(dataset_path, fieldnames, rows)
        experiment_root.mkdir(parents=True, exist_ok=True)

        asset_stats = self._build_asset_stats(candles_by_asset, stats)
        manifest = {
            "dataset_id": dataset_id,
            "selected_assets": list(selected_assets),
            "primary_interval": self.config.research.primary_interval,
            "row_count": row_count,
            "fieldnames": fieldnames,
            "research_settings": self.config.research.model_dump(mode="json"),
            "asset_stats": [entry.to_dict() for entry in asset_stats],
            "canonical_inputs": {
                asset: str(
                    canonical_candle_file(
                        self.data_settings.canonical_dir,
                        asset,
                        self.config.research.primary_interval,
                    )
                )
                for asset in selected_assets
            },
            "experiment_layout": {
                "root_dir": str(experiment_root),
                "required_files": ["manifest.json", "metrics.json", "predictions.csv"],
                "dataset_reference_field": "dataset_id",
            },
        }
        write_json(manifest_path, manifest)

        return FeatureBuildSummary(
            dataset_id=dataset_id,
            dataset_file=str(dataset_path),
            manifest_file=str(manifest_path),
            experiment_root=str(experiment_root),
            row_count=row_count,
            cached=False,
            selected_assets=selected_assets,
            asset_stats=asset_stats,
        )

    def _build_asset_stats(
        self,
        candles_by_asset: dict[str, list[Candle]],
        stats: dict[str, dict[str, int]],
    ) -> list[AssetDatasetStats]:
        summaries: list[AssetDatasetStats] = []
        for asset, candles in candles_by_asset.items():
            kraken_rows = sum(1 for candle in candles if candle.source.startswith("kraken"))
            fallback_rows = len(candles) - kraken_rows
            summaries.append(
                AssetDatasetStats(
                    asset=asset,
                    row_count=stats[asset]["row_count"],
                    first_timestamp=stats[asset]["first_timestamp"] or None,
                    last_timestamp=stats[asset]["last_timestamp"] or None,
                    kraken_rows=kraken_rows,
                    fallback_rows=fallback_rows,
                )
            )
        return summaries

    def _dataset_id(
        self,
        selected_assets: tuple[str, ...],
        candles_by_asset: dict[str, list[Candle]],
    ) -> str:
        digest = hashlib.sha256()
        settings_payload = self.config.research.model_dump(mode="json")
        digest.update(json.dumps(settings_payload, sort_keys=True).encode("utf-8"))
        digest.update(json.dumps(selected_assets).encode("utf-8"))
        for asset in selected_assets:
            path = canonical_candle_file(
                self.data_settings.canonical_dir,
                asset,
                self.config.research.primary_interval,
            )
            digest.update(asset.encode("utf-8"))
            digest.update(path.read_bytes())
            digest.update(str(len(candles_by_asset[asset])).encode("utf-8"))
        return digest.hexdigest()[:16]

    def _load_daily_candles(self, assets: tuple[str, ...]) -> dict[str, list[Candle]]:
        candles_by_asset: dict[str, list[Candle]] = {}
        for asset in assets:
            path = canonical_candle_file(
                self.data_settings.canonical_dir,
                asset,
                self.config.research.primary_interval,
            )
            if not path.exists():
                raise FileNotFoundError(f"Missing canonical daily candles for {asset}: {path}")
            candles = read_candles(path)
            if not candles:
                raise ValueError(f"Canonical daily candles are empty for {asset}: {path}")
            candles_by_asset[asset] = candles
        return candles_by_asset

    def _select_assets(self, assets: tuple[str, ...] | None) -> tuple[str, ...]:
        selected_assets = assets or FIXED_UNIVERSE
        invalid_assets = [asset for asset in selected_assets if asset not in FIXED_UNIVERSE]
        if invalid_assets:
            joined = ", ".join(sorted(invalid_assets))
            raise ValueError(f"Assets outside the fixed V1 universe are not allowed: {joined}")
        if "BTC" not in selected_assets:
            raise ValueError("Feature generation requires BTC for regime classification")
        return selected_assets

    def _summary_from_manifest(
        self,
        *,
        dataset_id: str,
        dataset_path: Path,
        manifest_path: Path,
        experiment_root: Path,
        manifest: dict[str, Any],
        cached: bool,
    ) -> FeatureBuildSummary:
        asset_stats_payload = manifest.get("asset_stats", [])
        asset_stats = [
            AssetDatasetStats(
                asset=entry["asset"],
                row_count=entry["row_count"],
                first_timestamp=entry["first_timestamp"],
                last_timestamp=entry["last_timestamp"],
                kraken_rows=entry["kraken_rows"],
                fallback_rows=entry["fallback_rows"],
            )
            for entry in asset_stats_payload
        ]
        return FeatureBuildSummary(
            dataset_id=dataset_id,
            dataset_file=str(dataset_path),
            manifest_file=str(manifest_path),
            experiment_root=str(experiment_root),
            row_count=int(manifest["row_count"]),
            cached=cached,
            selected_assets=tuple(manifest["selected_assets"]),
            asset_stats=asset_stats,
        )