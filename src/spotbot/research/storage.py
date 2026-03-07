"""Storage helpers for derived research datasets."""

from __future__ import annotations

import csv
from pathlib import Path


def feature_build_dir(root: Path, dataset_id: str) -> Path:
    """Return the storage directory for one derived dataset build."""
    return root / dataset_id


def feature_dataset_file(root: Path, dataset_id: str) -> Path:
    """Return the CSV dataset file path for one derived dataset build."""
    return feature_build_dir(root, dataset_id) / "dataset.csv"


def feature_manifest_file(root: Path, dataset_id: str) -> Path:
    """Return the manifest path for one derived dataset build."""
    return feature_build_dir(root, dataset_id) / "manifest.json"


def write_dataset_rows(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> int:
    """Write research dataset rows to CSV with a stable column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)