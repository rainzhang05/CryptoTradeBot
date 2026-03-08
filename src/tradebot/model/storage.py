"""Storage helpers for model artifacts and active-model pointers."""

from __future__ import annotations

import csv
from pathlib import Path


def model_dir(root: Path, model_id: str) -> Path:
    return root / model_id


def model_manifest_file(root: Path, model_id: str) -> Path:
    return model_dir(root, model_id) / "manifest.json"


def model_metrics_file(root: Path, model_id: str) -> Path:
    return model_dir(root, model_id) / "metrics.json"


def model_predictions_file(root: Path, model_id: str) -> Path:
    return model_dir(root, model_id) / "predictions.csv"


def model_bundle_file(root: Path, model_id: str) -> Path:
    return model_dir(root, model_id) / "bundle.pkl"


def latest_training_summary_file(root: Path) -> Path:
    return root / "latest_training_summary.json"


def latest_validation_summary_file(root: Path) -> Path:
    return root / "latest_validation_summary.json"


def latest_promotion_summary_file(root: Path) -> Path:
    return root / "latest_promotion_summary.json"


def active_model_pointer_file(root: Path) -> Path:
    return root / "production" / "latest_model.json"


def write_prediction_rows(path: Path, rows: list[dict[str, object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp",
                "asset",
                "expected_return_score",
                "downside_risk_score",
                "sell_risk_score",
                "actual_forward_return",
                "actual_downside_risk_flag",
                "actual_sell_risk_flag",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)
