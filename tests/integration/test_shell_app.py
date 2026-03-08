"""Headless tests for the interactive Tradebot shell."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from textual.widgets import Button, Input, OptionList, RichLog

import tradebot.shell as shell_module
from tradebot.cancellation import CommandCancelledError
from tradebot.config import initialize_app_home
from tradebot.shell import TradebotShellApp


def _transcript_text(app: TradebotShellApp) -> str:
    log = app.screen.query_one("#transcript", RichLog)
    return "\n".join(strip.text for strip in log.lines)


@pytest.mark.anyio
async def test_shell_first_run_prompt_bootstraps_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    app = TradebotShellApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#init-confirm", Button)
        await pilot.click("#init-confirm")
        await pilot.pause()

        assert (home / "config" / "settings.yaml").exists()
        assert "Tradebot home is ready" in _transcript_text(app)


@pytest.mark.anyio
async def test_shell_shows_command_suggestions_and_guided_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    initialize_app_home(home=home)
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    app = TradebotShellApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.screen.query_one("#command-input", Input)
        input_widget.value = "model tr"
        await pilot.pause()

        suggestions = app.screen.query_one("#command-suggestions", OptionList)
        assert suggestions.option_count > 0
        assert str(suggestions.get_option_at_index(0).prompt).startswith("model train")

        input_widget.value = "model train"
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen.query_one("#form-run", Button)


@pytest.mark.anyio
async def test_shell_dynamic_choice_provider_lists_model_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    initialize_app_home(home=home)
    (home / "artifacts" / "models" / "model-123").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    app = TradebotShellApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.screen.query_one("#command-input", Input)
        input_widget.value = "model validate"
        await pilot.press("enter")
        await pilot.pause()

        options = app.screen.query_one("#field-model_id-options", OptionList)
        assert options.option_count == 1
        assert str(options.get_option_at_index(0).prompt) == "model-123"


@pytest.mark.anyio
async def test_shell_ctrl_c_cancels_active_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    initialize_app_home(home=home)
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    def fake_execute_command(
        command_id: str,
        params=None,
        *,
        emitter=None,
        cancellation_token=None,
    ):
        assert command_id == "data_source"
        del params
        if emitter is not None:
            emitter(shell_module.ExecutionEvent("step_started", "Starting fake command."))
        while True:
            if cancellation_token is not None and cancellation_token.is_cancelled:
                raise CommandCancelledError("Command cancelled")
            time.sleep(0.01)

    monkeypatch.setattr(shell_module, "execute_command", fake_execute_command)

    app = TradebotShellApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.screen.query_one("#command-input", Input)
        input_widget.value = "data source"
        await pilot.press("enter")
        await pilot.pause()

        assert input_widget.disabled is True

        await pilot.press("ctrl+c")
        for _ in range(20):
            await pilot.pause()
            if not input_widget.disabled:
                break

        assert input_widget.disabled is False
        assert "Cancellation requested" in _transcript_text(app)
