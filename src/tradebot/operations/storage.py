"""Storage helpers for phase 9 operational state and reports."""

from __future__ import annotations

from pathlib import Path


def alert_state_file(root: Path) -> Path:
    """Return the durable alert deduplication state path."""
    return root / "alert_state.json"


def runtime_context_file(root: Path) -> Path:
    """Return the persisted latest runtime context path."""
    return root / "runtime_context.json"


def latest_alerts_report_file(root: Path) -> Path:
    """Return the operator-facing latest alerts report path."""
    return root / "reports" / "runtime" / "latest_alerts.json"


def latest_runtime_context_report_file(root: Path) -> Path:
    """Return the operator-facing latest runtime context report path."""
    return root / "reports" / "runtime" / "latest_runtime_context.json"
