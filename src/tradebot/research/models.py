"""Models for derived research datasets and build summaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AssetDatasetStats:
    """Summary for one asset inside a derived dataset."""

    asset: str
    row_count: int
    first_timestamp: int | None
    last_timestamp: int | None
    kraken_rows: int
    fallback_rows: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureBuildSummary:
    """Summary for one feature-store build."""

    dataset_id: str
    dataset_file: str
    manifest_file: str
    experiment_root: str
    row_count: int
    cached: bool
    selected_assets: tuple[str, ...]
    asset_stats: list[AssetDatasetStats]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_file": self.dataset_file,
            "manifest_file": self.manifest_file,
            "experiment_root": self.experiment_root,
            "row_count": self.row_count,
            "cached": self.cached,
            "selected_assets": list(self.selected_assets),
            "asset_stats": [stats.to_dict() for stats in self.asset_stats],
        }