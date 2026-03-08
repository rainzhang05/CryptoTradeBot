"""Structured logging helpers."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import TextIO

from pythonjsonlogger.json import JsonFormatter

from tradebot.config import AppConfig


def log_file(root: Path) -> Path:
    """Return the durable application log file path."""
    return root / "tradebot.log"


def configure_logging(config: AppConfig, stream: TextIO | None = None) -> None:
    """Configure application logging according to the active config."""
    paths = config.resolved_paths()
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    console_handler = logging.StreamHandler(stream or sys.stdout)
    file_handler = logging.FileHandler(log_file(paths.logs_dir), encoding="utf-8")
    formatter: logging.Formatter
    if config.app.log_format == "json":
        formatter = JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    file_formatter = JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime
    file_formatter.converter = time.gmtime

    console_handler.setFormatter(formatter)
    file_handler.setFormatter(file_formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(config.app.log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Keep terminal progress logs focused on application state rather than raw HTTP chatter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)
