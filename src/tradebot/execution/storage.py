"""Filesystem helpers for persisted live runtime state and reports."""

from __future__ import annotations

from pathlib import Path


def live_state_file(root: Path) -> Path:
    """Return the persisted live-state file path."""
    return root / "live_state.json"


def latest_live_status_file(root: Path) -> Path:
    """Return the operator-facing latest live status report path."""
    return root / "reports" / "runtime" / "latest_live_status.json"
