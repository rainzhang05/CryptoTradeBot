"""Unit tests for structured logging."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from spotbot.config import load_config
from spotbot.logging_config import configure_logging, get_logger


def test_configure_logging_emits_json(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app:
  environment: test
  log_level: INFO
  log_format: json
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )

    config = load_config(config_path=config_path, env_path=tmp_path / ".env")
    buffer = io.StringIO()

    configure_logging(config, stream=buffer)
    get_logger("spotbot.test").info("hello")

    output = buffer.getvalue()
    assert '"message": "hello"' in output
    assert '"name": "spotbot.test"' in output
    logging.getLogger().handlers.clear()