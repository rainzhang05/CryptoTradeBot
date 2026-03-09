"""Headless tests for the interactive Tradebot shell."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest
from textual.widgets import Button, Input, OptionList, RichLog

import tradebot.shell as shell_module
from tradebot.config import initialize_app_home
from tradebot.shell import TradebotShellApp


def _transcript_text(app: TradebotShellApp) -> str:
    log = app.screen.query_one("#transcript", RichLog)
    return "\n".join(strip.text for strip in log.lines)


@pytest.mark.anyio
async def test_shell_first_run_auto_bootstraps_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    app = TradebotShellApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert (home / "config" / "settings.yaml").exists()
        transcript = _transcript_text(app)
        transcript_widget = app.screen.query_one("#transcript", RichLog)
        suggestions = app.screen.query_one("#command-suggestions", OptionList)
        assert "Home:" in transcript
        assert "Config:" in transcript
        assert "Runtime:" in transcript
        assert "Session:" in transcript
        assert "Created your default Tradebot home" in transcript
        assert "Shell help" not in transcript
        assert transcript_widget.styles.scrollbar_size_vertical == 0
        assert transcript_widget.styles.scrollbar_size_horizontal == 0
        assert suggestions.styles.scrollbar_size_vertical == 0
        assert suggestions.styles.scrollbar_size_horizontal == 0


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
        suggestions = app.screen.query_one("#command-suggestions", OptionList)
        assert suggestions.display is False

        input_widget = app.screen.query_one("#command-input", Input)
        input_widget.value = "model tr"
        await pilot.pause()

        assert suggestions.display is True
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
async def test_shell_ctrl_c_requires_double_press_to_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    initialize_app_home(home=home)
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))
    times = iter((100.0, 106.0, 106.5))
    exit_calls: list[bool] = []

    monkeypatch.setattr(shell_module, "monotonic", lambda: next(times))

    app = TradebotShellApp()
    monkeypatch.setattr(app, "exit", lambda *args, **kwargs: exit_calls.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exit_calls == []
        assert "Press Ctrl+C again to exit the shell." in _transcript_text(app)

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exit_calls == []

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exit_calls == [True]


@pytest.mark.anyio
async def test_shell_clicked_suggestion_runs_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    initialize_app_home(home=home)
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    observed_commands: list[str] = []

    def fake_execute_command(
        command_id: str,
        params=None,
        *,
        emitter=None,
        cancellation_token=None,
    ):
        del params, emitter, cancellation_token
        observed_commands.append(command_id)
        if command_id == "status":
            return {"active_model": None}
        return {"ok": True}

    monkeypatch.setattr(shell_module, "execute_command", fake_execute_command)

    app = TradebotShellApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.screen.query_one("#command-input", Input)
        input_widget.value = "doctor"
        await pilot.pause()

        suggestions = app.screen.query_one("#command-suggestions", OptionList)
        app.on_option_list_option_selected(OptionList.OptionSelected(suggestions, 0))
        await pilot.pause()

        assert "doctor" in observed_commands
        assert "Running command." in _transcript_text(app)
        assert "doctor" in _transcript_text(app)


@pytest.mark.anyio
async def test_shell_rejects_new_commands_while_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    initialize_app_home(home=home)
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    observed_commands: list[str] = []

    def fake_execute_command(
        command_id: str,
        params=None,
        *,
        emitter=None,
        cancellation_token=None,
    ):
        del params, emitter, cancellation_token
        observed_commands.append(command_id)
        return {"ok": True}

    monkeypatch.setattr(shell_module, "execute_command", fake_execute_command)

    app = TradebotShellApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.screen.query_one("#command-input", Input)
        input_widget.value = "doctor"
        await pilot.pause()
        observed_commands.clear()

        busy_task = asyncio.create_task(asyncio.sleep(60))
        app.active_task = busy_task

        app.on_input_submitted(Input.Submitted(input_widget, input_widget.value))
        await pilot.pause()

        suggestions = app.screen.query_one("#command-suggestions", OptionList)
        app.on_option_list_option_selected(OptionList.OptionSelected(suggestions, 0))
        await pilot.pause()

        app.active_task = None
        busy_task.cancel()
        with suppress(asyncio.CancelledError):
            await busy_task

        transcript = _transcript_text(app)
        normalized_transcript = " ".join(transcript.split())
        assert observed_commands == []
        assert transcript.count("Another command is already running.") == 2
        assert "press Ctrl+C twice within 5 seconds to exit the shell" in normalized_transcript
