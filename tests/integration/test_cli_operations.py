"""Integration tests for Phase 8 operator CLI commands."""

from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path

from typer.testing import CliRunner

from tradebot.cli import app
from tradebot.config import load_config
from tradebot.logging_config import configure_logging
from tradebot.runtime import runtime_process_file

runner = CliRunner()


def _write_config(root: Path, *, default_mode: str = "simulate") -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        f"""
app:
  log_format: console
runtime:
  default_mode: {default_mode}
  max_cycles: 1
exchange: {{}}
data:
  canonical_dir: data/canonical
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {{}}
paths: {{}}
""",
        encoding="utf-8",
    )
    return config_path


def test_status_command_reads_runtime_state(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    process_path = runtime_process_file(tmp_path / "runtime" / "state")
    process_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "mode": "live",
                "started_at": "2026-03-08T00:00:00+00:00",
                "config_path": str(config_path),
            }
        ),
        encoding="utf-8",
    )
    live_status_path = (
        tmp_path / "artifacts" / "reports" / "runtime" / "latest_live_status.json"
    )
    live_status_path.parent.mkdir(parents=True, exist_ok=True)
    live_status_path.write_text(
        json.dumps({"status": "ok", "holdings": {"BTC": 0.5}}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert '"managed_process":' in result.stdout
    assert '"running": true' in result.stdout
    assert '"live_status":' in result.stdout


def test_report_list_and_export_commands(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    report_path = tmp_path / "artifacts" / "reports" / "models" / "latest_validation_summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text('{"model_id":"model-1"}', encoding="utf-8")
    export_path = tmp_path / "exports" / "validation.json"

    list_result = runner.invoke(app, ["report", "list"])
    export_result = runner.invoke(
        app,
        [
            "report",
            "export",
            "artifacts/reports/models/latest_validation_summary.json",
            str(export_path),
        ],
    )

    assert list_result.exit_code == 0
    assert "artifacts/reports/models/latest_validation_summary.json" in list_result.stdout
    assert export_result.exit_code == 0
    assert export_path.exists()
    assert '"destination":' in export_result.stdout


def test_logs_tail_command_reads_durable_log_file(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")
    configure_logging(config, stream=io.StringIO())
    logging.getLogger("tradebot.test").info("phase8-log-line")
    logging.getLogger().handlers.clear()

    result = runner.invoke(app, ["logs", "tail", "--lines", "1"])

    assert result.exit_code == 0
    assert "phase8-log-line" in result.stdout


def test_email_set_updates_yaml_config(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

    result = runner.invoke(app, ["email", "set", "trader@example.com"])

    assert result.exit_code == 0
    assert "trader@example.com" in config_path.read_text(encoding="utf-8")


def test_email_test_command_uses_smtp(tmp_path: Path, monkeypatch) -> None:
    import tradebot.operations.service as operations_service

    config_path = _write_config(tmp_path)
    updated_config = config_path.read_text(encoding="utf-8").replace(
        "alerts: {}",
        "alerts:\n  email_recipient: trader@example.com",
    )
    config_path.write_text(
        updated_config,
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    sent_messages: list[str] = []

    class FakeSMTP:
        def ehlo(self) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            assert username == "bot@example.com"
            assert password == "secret"

        def send_message(self, message) -> None:
            sent_messages.append(str(message["To"]))

        def quit(self) -> None:
            return None

    monkeypatch.setattr(
        operations_service,
        "_default_smtp_factory",
        lambda host, port: FakeSMTP(),
    )

    result = runner.invoke(app, ["email", "test"])

    assert result.exit_code == 0
    assert sent_messages == ["trader@example.com"]
    assert '"recipient": "trader@example.com"' in result.stdout


def test_doctor_validates_exchange_connectivity(tmp_path: Path, monkeypatch) -> None:
    import tradebot.operations.service as operations_service

    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("KRAKEN_API_KEY", "test-key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "dGVzdA==")

    class FakeKrakenClient:
        def __init__(self, *, api_key=None, api_secret=None, otp=None) -> None:
            del api_key, api_secret, otp

        def get_system_status(self) -> dict[str, str | None]:
            return {"status": "online", "timestamp": "123", "message": None}

        def get_balances(self) -> dict[str, float]:
            return {"ZUSD": 100.0}

    monkeypatch.setattr(operations_service, "KrakenClient", FakeKrakenClient)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"public_api": {' in result.stdout
    assert '"private_api": {' in result.stdout


def test_stop_command_requests_runtime_termination(tmp_path: Path, monkeypatch) -> None:
    import tradebot.operations.service as operations_service

    config_path = _write_config(tmp_path)
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
    process_path = runtime_process_file(tmp_path / "runtime" / "state")
    process_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "mode": "simulate",
                "started_at": "2026-03-08T00:00:00+00:00",
                "config_path": str(config_path),
            }
        ),
        encoding="utf-8",
    )
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        operations_service.os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)),
    )

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert signals
    assert '"status": "termination_requested"' in result.stdout
