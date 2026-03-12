"""Integration tests for the CLI skeleton and entrypoint routing."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import tradebot.cli as cli_module
import tradebot.commanding as commanding_module
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


def test_init_command_bootstraps_application_home(tmp_path: Path) -> None:
    home = tmp_path / "tradebot-home"

    result = runner.invoke(app, ["init", "--home", str(home)])

    assert result.exit_code == 0
    assert (home / "config" / "settings.yaml").exists()
    assert (home / ".env").exists()
    assert (home / "data").exists()
    assert (home / "artifacts").exists()
    assert (home / "runtime").exists()


def test_config_validate_auto_bootstraps_default_application_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "tradebot-home"
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    result = runner.invoke(app, ["config", "validate"])

    assert result.exit_code == 0
    assert "Configuration valid" in result.stdout
    assert (home / "config" / "settings.yaml").exists()
    assert (home / ".env").exists()


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

    result = runner.invoke(
        app,
        [
            "run",
            "--mode",
            "simulate",
            "--max-cycles",
            "1",
            "--strategy-preset",
            "max_profit",
        ],
    )

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
        def __init__(self, config: object) -> None:
            self.config = config

        def run(
            self,
            mode: str,
            max_cycles: int | None = None,
            *,
            dataset_track: str | None = None,
            cancellation_token=None,
            on_cycle=None,
            on_alert=None,
        ):
            del max_cycles, dataset_track, cancellation_token
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
                        "to_dict": lambda self: {
                            "severity": "critical",
                            "event_class": "freeze_triggered",
                            "mode": mode,
                            "message": "freeze",
                            "email_sent": False,
                            "email_error": "email_recipient_not_configured",
                        },
                    },
                )()
                on_alert(alert)
            return [snapshot]

    monkeypatch.setattr(commanding_module, "RuntimeService", FakeRuntimeService)

    result = runner.invoke(app, ["run", "--mode", "live"])

    assert result.exit_code == 0
    assert "mode=live" in result.stdout
    assert "ALERT | severity=critical | class=freeze_triggered" in result.stdout
    assert "system=online" in result.stdout
    assert "holdings=BTC:0.50000000" in result.stdout
    assert "Completed 1 cycle(s) in live mode." in result.stdout


def test_main_launches_shell_on_interactive_tty(monkeypatch) -> None:
    launched: list[bool] = []

    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli_module, "launch_shell", lambda: launched.append(True))

    cli_module.main([])

    assert launched == [True]


def test_main_prints_help_in_non_interactive_no_arg_mode(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_app(*, prog_name: str, args: list[str]) -> None:
        assert prog_name == "tradebot"
        captured.append(args)

    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False)
    monkeypatch.setattr(cli_module, "app", fake_app)

    cli_module.main([])

    assert captured == [["--help"]]
