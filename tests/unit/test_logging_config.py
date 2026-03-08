"""Unit tests for structured logging."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from tradebot.config import load_config
from tradebot.logging_config import configure_logging, get_logger, log_file


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
    get_logger("tradebot.test").info("hello")

    output = buffer.getvalue()
    assert '"message": "hello"' in output
    assert '"name": "tradebot.test"' in output
    durable_log = log_file(config.resolved_paths().logs_dir)
    assert durable_log.exists()
    assert '"message": "hello"' in durable_log.read_text(encoding="utf-8")
    logging.getLogger().handlers.clear()
