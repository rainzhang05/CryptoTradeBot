"""Unit tests for runtime alert classification and delivery."""

from __future__ import annotations

import json
from pathlib import Path

from tradebot.config import load_config
from tradebot.operations.alerts import RuntimeAlertService
from tradebot.operations.storage import alert_state_file
from tradebot.runtime import RuntimeSnapshot


def test_runtime_alert_service_sends_and_deduplicates_emails(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts:
  email_recipient: trader@example.com
paths: {}
""",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        """
SMTP_HOST=smtp.example.com
SMTP_USERNAME=bot@example.com
SMTP_PASSWORD=secret
""".strip(),
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=env_path)

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
            sent_messages.append(str(message["Subject"]))

        def quit(self) -> None:
            return None

    service = RuntimeAlertService(config, smtp_factory=lambda host, port: FakeSMTP())
    snapshot = RuntimeSnapshot(
        mode="live",
        cycle=1,
        status="frozen",
        system_status="online",
        connectivity_state="degraded",
        timestamp=1_705_000_000,
        risk_state="normal",
        incidents=["missing_active_model"],
        freeze_reason="missing_active_model",
    )

    first_events = service.process_snapshot(snapshot)
    second_events = service.process_snapshot(snapshot)

    assert [event.event_class for event in first_events] == [
        "freeze_triggered",
        "model_inference_failure",
    ]
    assert all(event.email_sent for event in first_events)
    assert second_events == []
    assert len(sent_messages) == 2

    state_payload = json.loads(
        alert_state_file(config.resolved_paths().state_dir).read_text(encoding="utf-8")
    )
    assert "freeze:live:1705000000:missing_active_model" in state_payload["sent_keys"]


def test_runtime_alert_service_emits_drawdown_and_startup_events(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
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
    service = RuntimeAlertService(config)

    startup_events = service.process_startup_failure(mode="simulate", error="bad config")
    drawdown_events = service.process_snapshot(
        RuntimeSnapshot(
            mode="simulate",
            cycle=1,
            status="ok",
            system_status="simulated",
            connectivity_state="simulated",
            timestamp=1_705_000_001,
            risk_state="elevated_caution",
            portfolio_drawdown=-0.11,
        )
    )
    catastrophe_events = service.process_snapshot(
        RuntimeSnapshot(
            mode="simulate",
            cycle=2,
            status="ok",
            system_status="simulated",
            connectivity_state="simulated",
            timestamp=1_705_000_002,
            risk_state="catastrophe",
            fill_count=1,
            fills=[{"asset": "BTC", "side": "sell", "quantity": 0.1}],
            portfolio_drawdown=-0.33,
            decision_actions={"BTC": "exit"},
            decision_reasons={"BTC": "catastrophe"},
        )
    )

    assert startup_events[0].event_class == "startup_failure"
    assert startup_events[0].email_error == "email_recipient_not_configured"
    assert [event.event_class for event in drawdown_events] == ["portfolio_drawdown_threshold"]
    assert [event.event_class for event in catastrophe_events] == [
        "trade_executed",
        "risk_reduction_triggered",
        "catastrophe_state_entered",
    ]
