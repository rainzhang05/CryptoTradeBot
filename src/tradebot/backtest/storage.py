"""Artifact storage helpers for backtests and simulation state."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def backtest_run_dir(root: Path, run_id: str) -> Path:
    return root / "backtests" / run_id


def backtest_report_file(root: Path, run_id: str) -> Path:
    return backtest_run_dir(root, run_id) / "report.json"


def backtest_fills_file(root: Path, run_id: str) -> Path:
    return backtest_run_dir(root, run_id) / "fills.csv"


def backtest_equity_curve_file(root: Path, run_id: str) -> Path:
    return backtest_run_dir(root, run_id) / "equity_curve.csv"


def backtest_decisions_file(root: Path, run_id: str) -> Path:
    return backtest_run_dir(root, run_id) / "decisions.csv"


def latest_backtest_report_file(root: Path) -> Path:
    return root / "reports" / "backtests" / "latest_backtest_report.json"


def simulate_state_file(root: Path) -> Path:
    return root / "simulate_state.json"


def write_csv_rows(path: Path, *, fieldnames: list[str], rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)