"""Structured logging helpers."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

from pythonjsonlogger.json import JsonFormatter

from tradebot.config import AppConfig


def configure_logging(config: AppConfig, stream: TextIO | None = None) -> None:
    """Configure application logging according to the active config."""
    handler = logging.StreamHandler(stream or sys.stdout)
    formatter: logging.Formatter
    if config.app.log_format == "json":
        formatter = JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(config.app.log_level)
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)