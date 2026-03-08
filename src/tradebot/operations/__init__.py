"""Operational services for runtime operations and alerts."""

from __future__ import annotations

from typing import Any

__all__ = ["OperationsService", "RuntimeAlertService"]


def __getattr__(name: str) -> Any:
    if name == "OperationsService":
        from tradebot.operations.service import OperationsService

        return OperationsService
    if name == "RuntimeAlertService":
        from tradebot.operations.alerts import RuntimeAlertService

        return RuntimeAlertService
    raise AttributeError(name)
