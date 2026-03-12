"""Summaries and references for model artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelTrainingSummary:
    model_id: str
    dataset_id: str
    artifact_dir: str
    manifest_file: str
    metrics_file: str
    predictions_file: str
    bundle_file: str
    split_count: int
    validation_row_count: int
    selected_assets: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_assets"] = list(self.selected_assets)
        return payload


@dataclass(frozen=True)
class ModelValidationSummary:
    model_id: str
    dataset_id: str
    manifest_file: str
    metrics_file: str
    split_count: int
    validation_row_count: int
    expected_return_mae: float
    expected_return_correlation: float
    expected_return_directional_accuracy: float
    downside_brier_score: float
    sell_brier_score: float
    promotion_eligible: bool
    selected_assets: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_assets"] = list(self.selected_assets)
        return payload


@dataclass(frozen=True)
class ModelPromotionSummary:
    model_id: str
    dataset_id: str
    pointer_file: str
    manifest_file: str
    previous_model_id: str | None
    promoted_at: str
    hybrid_backtest_run_id: str | None = None
    hybrid_total_return: float | None = None
    rule_only_backtest_run_id: str | None = None
    rule_only_total_return: float | None = None
    incremental_total_return: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActiveModelReference:
    model_id: str
    dataset_id: str
    dataset_track: str
    manifest_file: str
    metrics_file: str
    predictions_file: str
    bundle_file: str
    selected_assets: tuple[str, ...]
    research_settings_signature: str
    feature_column_signature: str
    model_family: str
    prediction_start_timestamp: int | None = None
    prediction_end_timestamp: int | None = None
