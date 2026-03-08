"""Integration tests for the CLI skeleton."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import tradebot.cli as cli_module
from tradebot.cli import app
from tradebot.runtime import RuntimeSnapshot

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_config_path_command(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

    result = runner.invoke(app, ["config-path"])

    assert result.exit_code == 0
    assert str(config_path.resolve()) in result.stdout


def test_config_validate_command(tmp_path: Path, monkeypatch) -> None:
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
        monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

        result = runner.invoke(app, ["config", "validate"])

        assert result.exit_code == 0
        assert "Configuration valid" in result.stdout


def test_run_command(tmp_path: Path, monkeypatch) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "settings.yaml"
        config_path.write_text(
                """
app:
    log_format: console
runtime:
    default_mode: simulate
    max_cycles: 1
exchange: {}
strategy:
    fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
                encoding="utf-8",
        )
        monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))

        result = runner.invoke(app, ["run", "--mode", "simulate", "--max-cycles", "1"])

        assert result.exit_code == 0
        assert "Completed 1 cycle(s) in simulate mode." in result.stdout


def test_run_live_command_renders_monitoring_output(tmp_path: Path, monkeypatch) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "settings.yaml"
        config_path.write_text(
                """
app:
    log_format: console
runtime:
    default_mode: live
    max_cycles: 1
exchange: {}
strategy:
    fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
                encoding="utf-8",
        )
        monkeypatch.setenv("BOT_CONFIG_PATH", str(config_path))
        monkeypatch.setenv("KRAKEN_API_KEY", "test-key")
        monkeypatch.setenv("KRAKEN_API_SECRET", "dGVzdA==")

        class FakeRuntimeService:
            def __init__(self, config):
                self.config = config

            def run(
                self,
                mode: str,
                max_cycles: int | None = None,
                *,
                on_cycle=None,
                on_alert=None,
            ):
                snapshot = RuntimeSnapshot(
                    mode=mode,
                    cycle=1,
                    status="ok",
                    system_status="online",
                    connectivity_state="online",
                    timestamp=1_705_000_000,
                    regime_state="constructive",
                    risk_state="normal",
                    equity_usd=1_050.0,
                    cash_usd=900.0,
                    fill_count=1,
                    holdings={"BTC": 0.5},
                    open_order_count=0,
                    incidents=["trade_executed"],
                    model_id="model-1",
                    decision_executed=True,
                )
                if on_cycle is not None:
                    on_cycle(snapshot)
                if on_alert is not None:
                    alert = type(
                        "Alert",
                        (),
                        {
                            "severity": "critical",
                            "event_class": "freeze_triggered",
                            "mode": mode,
                            "message": "freeze",
                            "email_sent": False,
                            "email_error": "email_recipient_not_configured",
                        },
                    )()
                    on_alert(alert)
                return [snapshot]

        monkeypatch.setattr(cli_module, "RuntimeService", FakeRuntimeService)

        result = runner.invoke(app, ["run", "--mode", "live"])

        assert result.exit_code == 0
        assert "mode=live" in result.stdout
        assert "ALERT | severity=critical | class=freeze_triggered" in result.stdout
        assert "system=online" in result.stdout
        assert "holdings=BTC:0.50000000" in result.stdout
        assert "Completed 1 cycle(s) in live mode." in result.stdout
