"""Shared cooperative cancellation primitives."""

from __future__ import annotations

import threading


class CommandCancelledError(RuntimeError):
    """Raised when a shell command is cooperatively cancelled."""


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CommandCancelledError("Command cancelled")
