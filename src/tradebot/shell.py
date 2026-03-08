"""Interactive Textual shell for Tradebot."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Input,
    OptionList,
    RichLog,
    SelectionList,
    Static,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from tradebot import __version__
from tradebot.cancellation import CancellationToken
from tradebot.commanding import (
    CommandFieldSpec,
    CommandSpec,
    ExecutionEvent,
    RuntimeRunResult,
    all_command_specs,
    command_choices,
    default_form_values,
    execute_command,
    parse_shell_command,
    safe_config_summary,
)
from tradebot.config import default_config_path, ensure_app_home_initialized

VIOLET_BORDER = "#8b5cf6"
VIOLET_BORDER_FOCUS = "#a78bfa"
VIOLET_BORDER_SUBTLE = "#6d28d9"

QUICK_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("quick-action-status", "Status", "status"),
    ("quick-action-doctor", "Doctor", "doctor"),
    ("quick-action-config-validate", "Config Validate", "config validate"),
    ("quick-action-data-source", "Data Source", "data source"),
    ("quick-action-report-list", "Report List", "report list"),
)

QUICK_ACTIONS_BY_ID = {
    action_id: command_text for action_id, _label, command_text in QUICK_ACTIONS
}

ASCII_ROBOT = r"""
   [ ] [ ]
    |   |
  .-=====-. 
  |  o o  |
  |   ^   |
  | \___/ |
   '-----'
"""


def _stringify_prompt(prompt: object) -> str:
    return str(prompt)


@dataclass(frozen=True)
class CommandSubmission:
    """One shell command ready for execution."""

    spec: CommandSpec
    params: dict[str, object]
    text: str


class CommandFormScreen(ModalScreen[dict[str, object] | None]):
    """Guided parameter-selection screen for shell commands."""

    CSS = """
    CommandFormScreen {
        align: center middle;
    }

    #command-form {
        width: 96;
        height: 80%;
        border: round #8b5cf6;
        background: $surface;
        padding: 1 2;
    }

    Input {
        border: round #6d28d9;
    }

    Input:focus {
        border: round #a78bfa;
    }

    OptionList,
    SelectionList {
        border: round #6d28d9;
    }

    OptionList:focus,
    SelectionList:focus {
        border: round #a78bfa;
    }

    .field-block {
        height: auto;
        margin-bottom: 1;
    }

    .field-help {
        color: $text-muted;
        margin-bottom: 1;
    }

    .field-options {
        height: 6;
        margin-bottom: 1;
    }

    .field-selection {
        height: 10;
        margin-bottom: 1;
    }

    #form-error {
        color: $error;
        height: auto;
    }

    #form-actions {
        height: auto;
        content-align: center middle;
    }
    """

    def __init__(self, spec: CommandSpec, initial_values: dict[str, object]) -> None:
        super().__init__()
        self.spec = spec
        self.initial_values = initial_values
        self._choices_by_field = {
            field_spec.name: field_spec.resolved_choices()
            for field_spec in self.spec.fields
            if field_spec.resolved_choices()
        }

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="command-form"):
            yield Static(f"Configure {' '.join(self.spec.tokens)}", classes="title")
            yield Static(self.spec.description, classes="field-help")
            for field_spec in self.spec.fields:
                yield from self._compose_field(field_spec)
            yield Static("", id="form-error")
            with Horizontal(id="form-actions"):
                yield Button("Run", id="form-run", variant="success")
                yield Button("Cancel", id="form-cancel")

    def _compose_field(self, field_spec: CommandFieldSpec) -> ComposeResult:
        with Vertical(classes="field-block"):
            label = field_spec.label
            if field_spec.required:
                label = f"{label} *"
            yield Static(label)
            if field_spec.multiple and field_spec.resolved_choices():
                selections = [
                    Selection(choice, choice, choice in self._initial_multiple_values(field_spec))
                    for choice in field_spec.resolved_choices()
                ]
                yield SelectionList(
                    *selections,
                    id=f"field-{field_spec.name}-selection",
                    classes="field-selection",
                )
            elif field_spec.value_type == "bool":
                yield Checkbox(
                    field_spec.help or field_spec.label,
                    value=bool(self.initial_values.get(field_spec.name, field_spec.default)),
                    id=f"field-{field_spec.name}-checkbox",
                )
            else:
                yield Input(
                    value=self._initial_input_value(field_spec),
                    placeholder=field_spec.help,
                    id=f"field-{field_spec.name}-input",
                )
                if field_spec.resolved_choices():
                    yield OptionList(id=f"field-{field_spec.name}-options", classes="field-options")
            if field_spec.help and field_spec.value_type != "bool":
                yield Static(field_spec.help, classes="field-help")

    def on_mount(self) -> None:
        for field_spec in self.spec.fields:
            if (
                field_spec.resolved_choices()
                and not field_spec.multiple
                and field_spec.value_type != "bool"
            ):
                self._refresh_field_options(field_spec.name)

    def on_input_changed(self, event: Input.Changed) -> None:
        if not event.input.id or not event.input.id.endswith("-input"):
            return
        field_name = event.input.id.removeprefix("field-").removesuffix("-input")
        if field_name in self._choices_by_field:
            self._refresh_field_options(field_name)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option_list.id or not event.option_list.id.endswith("-options"):
            return
        field_name = event.option_list.id.removeprefix("field-").removesuffix("-options")
        input_widget = self.query_one(f"#field-{field_name}-input", Input)
        input_widget.value = _stringify_prompt(event.option.prompt)
        input_widget.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-cancel":
            self.dismiss(None)
            return
        if event.button.id == "form-run":
            try:
                params = self._collect_params()
            except ValueError as exc:
                self.query_one("#form-error", Static).update(str(exc))
                return
            self.dismiss(params)

    def _initial_input_value(self, field_spec: CommandFieldSpec) -> str:
        value = self.initial_values.get(field_spec.name, field_spec.default)
        if value is None:
            return ""
        return str(value)

    def _initial_multiple_values(self, field_spec: CommandFieldSpec) -> list[str]:
        value = self.initial_values.get(field_spec.name, [])
        if isinstance(value, list):
            return [str(entry) for entry in value]
        if isinstance(value, tuple):
            return [str(entry) for entry in value]
        return []

    def _refresh_field_options(self, field_name: str) -> None:
        choices = self._choices_by_field.get(field_name, [])
        input_widget = self.query_one(f"#field-{field_name}-input", Input)
        option_list = self.query_one(f"#field-{field_name}-options", OptionList)
        normalized = input_widget.value.strip().lower()
        filtered = [
            choice for choice in choices if not normalized or choice.lower().startswith(normalized)
        ]
        option_list.clear_options()
        option_list.add_options([Option(choice, id=choice) for choice in filtered[:12]])

    def _collect_params(self) -> dict[str, object]:
        params: dict[str, object] = {}
        for field_spec in self.spec.fields:
            if field_spec.multiple and field_spec.resolved_choices():
                selection_widget = self.query_one(
                    f"#field-{field_spec.name}-selection",
                    SelectionList,
                )
                value: object = list(selection_widget.selected)
            elif field_spec.value_type == "bool":
                checkbox = self.query_one(f"#field-{field_spec.name}-checkbox", Checkbox)
                value = checkbox.value
            else:
                input_widget = self.query_one(f"#field-{field_spec.name}-input", Input)
                raw_value = input_widget.value.strip()
                if field_spec.value_type == "int":
                    value = None if raw_value == "" else int(raw_value)
                else:
                    value = None if raw_value == "" else raw_value
            if field_spec.required and value in {None, "", []}:
                raise ValueError(f"{field_spec.label} is required.")
            params[field_spec.name] = value
        return params


class TradebotShellApp(App[None]):
    """Interactive operator shell for the Tradebot command surface."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #brand {
        height: auto;
        content-align: center middle;
        border: round #8b5cf6;
        padding: 1;
        margin: 1 1 0 1;
    }

    #body {
        height: 1fr;
        margin: 0 1;
    }

    #transcript {
        width: 1fr;
        border: round #6d28d9;
    }

    #sidebar {
        width: 34;
        border: round #6d28d9;
        padding: 0 1;
    }

    #sidebar-actions {
        height: auto;
        border: round #6d28d9;
        padding: 1;
        margin-bottom: 1;
    }

    .sidebar-block {
        margin-bottom: 1;
        height: auto;
    }

    .sidebar-title {
        text-style: bold;
    }

    #input-region {
        height: auto;
        margin: 0 1 1 1;
    }

    #command-suggestions {
        height: 8;
        border: round #6d28d9;
    }

    #command-suggestions:focus {
        border: round #a78bfa;
    }

    #command-input {
        dock: bottom;
        border: round #6d28d9;
    }

    #command-input:focus {
        border: round #a78bfa;
    }

    .quick-action-button {
        width: 100%;
        margin-bottom: 1;
        border: round #6d28d9;
    }

    .quick-action-button:focus {
        border: round #a78bfa;
    }
    """

    BINDINGS = [
        ("ctrl+c", "cancel", "Cancel command"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.active_token: CancellationToken | None = None
        self.active_task: asyncio.Task[None] | None = None
        self.current_command: str = "idle"

    def _main_screen(self) -> Screen[object]:
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield Static(
            f"{ASCII_ROBOT}\nTradebot  v{__version__}\nInteractive operator shell",
            id="brand",
        )
        with Horizontal(id="body"):
            yield RichLog(id="transcript", wrap=True, markup=False)
            with Vertical(id="sidebar"):
                yield Static("", id="sidebar-home", classes="sidebar-block")
                yield Static("", id="sidebar-config", classes="sidebar-block")
                yield Static("", id="sidebar-runtime", classes="sidebar-block")
                yield Static("", id="sidebar-context", classes="sidebar-block")
                with Vertical(id="sidebar-actions", classes="sidebar-block"):
                    yield Static("[Quick actions]")
                    for action_id, label, _command in QUICK_ACTIONS:
                        yield Button(label, id=action_id, classes="quick-action-button")
                yield Static(
                    "[Shortcuts]\n"
                    "help\n"
                    "clear\n"
                    "exit\n"
                    "click a quick action or suggestion to run it\n"
                    "ctrl+c cancel active command",
                    id="sidebar-shortcuts",
                    classes="sidebar-block",
                )
        with Vertical(id="input-region"):
            yield OptionList(id="command-suggestions")
            yield Input(
                placeholder="Type a command like model train, data source, help, clear, or exit",
                id="command-input",
            )
        yield Footer()

    def on_mount(self) -> None:
        bootstrap_summary = ensure_app_home_initialized()
        self._write_line("Tradebot shell ready.")
        if bootstrap_summary is not None:
            self._write_line(
                "Created default Tradebot home at "
                f"{bootstrap_summary['home']} with starter config and env files."
            )
        self._update_sidebar()
        self._refresh_command_suggestions("")
        self._main_screen().query_one("#command-input", Input).focus()

    def action_cancel(self) -> None:
        if self.active_token is None:
            self._write_line("No active command to cancel.")
            return
        self.active_token.cancel()
        self._write_line("Cancellation requested. Waiting for the active command to stop...")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "command-input":
            return
        self.current_command = event.value.strip() or "idle"
        self._refresh_command_suggestions(event.value)
        self._update_sidebar()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command-input":
            return
        if self.active_task is not None:
            self._write_line("Another command is already running.")
            return
        text = event.value.strip()
        if not text:
            return
        self._main_screen().query_one("#command-input", Input).value = ""
        self._handle_shell_input(text)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "command-suggestions":
            return
        if self.active_task is not None:
            self._write_line("Another command is already running.")
            return
        text = _stringify_prompt(event.option.prompt)
        self._main_screen().query_one("#command-input", Input).value = ""
        self._handle_shell_input(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id not in QUICK_ACTIONS_BY_ID:
            return
        if self.active_task is not None:
            self._write_line("Another command is already running.")
            return
        self._handle_shell_input(QUICK_ACTIONS_BY_ID[button_id])

    def _handle_shell_input(self, text: str) -> None:
        normalized = text.strip()
        lowered = normalized.lower()
        if lowered == "help":
            self._render_help()
            return
        if lowered == "clear":
            self._main_screen().query_one("#transcript", RichLog).clear()
            self._write_line("Transcript cleared.")
            return
        if lowered == "exit":
            self.exit()
            return

        try:
            parsed = parse_shell_command(normalized)
        except Exception as exc:
            self._write_line(str(exc))
            return

        if parsed.spec.fields and not parsed.used_inline_arguments:
            self.push_screen(
                CommandFormScreen(parsed.spec, default_form_values(parsed.spec)),
                lambda params: self._run_form_submission(parsed.spec, normalized, params),
            )
            return

        self._submit_command(
            CommandSubmission(spec=parsed.spec, params=parsed.params, text=normalized)
        )

    def _run_form_submission(
        self,
        spec: CommandSpec,
        text: str,
        params: dict[str, object] | None,
    ) -> None:
        if params is None:
            self._write_line(f"Cancelled parameter entry for {' '.join(spec.tokens)}.")
            return
        self._submit_command(CommandSubmission(spec=spec, params=params, text=text))

    def _submit_command(self, submission: CommandSubmission) -> None:
        self._write_line(f"> {submission.text}")
        self.current_command = submission.text
        self._set_busy(True)
        self.active_token = CancellationToken()
        self.active_task = asyncio.create_task(self._run_submission(submission))
        self._update_sidebar()

    async def _run_submission(self, submission: CommandSubmission) -> None:
        try:
            result = await asyncio.to_thread(
                execute_command,
                submission.spec.id,
                submission.params,
                emitter=lambda event: self.call_from_thread(self._handle_execution_event, event),
                cancellation_token=self.active_token,
            )
            self._render_command_result(submission.spec.id, result)
        except Exception as exc:
            self._write_line(str(exc))
        finally:
            self.active_task = None
            self.active_token = None
            self.current_command = "idle"
            self._set_busy(False)
            self._update_sidebar()

    def _handle_execution_event(self, event: ExecutionEvent) -> None:
        self._write_line(self._format_event(event))

    def _render_command_result(self, command_id: str, result: object) -> None:
        if command_id == "logs_tail" and isinstance(result, list):
            for line in result:
                self._write_line(str(line))
            return
        if command_id == "report_list" and isinstance(result, list):
            for entry in result[:25]:
                path = entry.get("path", "")
                category = entry.get("category", "")
                self._write_line(f"[{category}] {path}")
            if len(result) > 25:
                self._write_line(f"... {len(result) - 25} more entries")
            return
        if isinstance(result, str):
            self._write_line(result)
            return
        if isinstance(result, RuntimeRunResult):
            self._write_line(
                f"Completed {result.completed_cycles} cycle(s) in {result.mode} mode."
            )
            return
        if isinstance(result, dict):
            self._write_line(self._format_mapping(result))
            return
        self._write_line(json.dumps(result, indent=2, sort_keys=True))

    def _render_help(self) -> None:
        self._write_line("Available commands:")
        for spec in all_command_specs():
            self._write_line(f"  {' '.join(spec.tokens)}  - {spec.description}")
        self._write_line("Shell commands: help, clear, exit")

    def _refresh_command_suggestions(self, prefix: str) -> None:
        option_list = self._main_screen().query_one("#command-suggestions", OptionList)
        option_list.clear_options()
        matches = command_choices(prefix)
        option_list.add_options([Option(match, id=match) for match in matches[:12]])

    def _set_busy(self, busy: bool) -> None:
        input_widget = self._main_screen().query_one("#command-input", Input)
        suggestions = self._main_screen().query_one("#command-suggestions", OptionList)
        input_widget.disabled = busy
        suggestions.disabled = busy
        for button in self._main_screen().query(".quick-action-button"):
            button.disabled = busy
        if not busy:
            input_widget.focus()

    def _write_line(self, line: str) -> None:
        self._main_screen().query_one("#transcript", RichLog).write(line)

    def _update_sidebar(self) -> None:
        summary = safe_config_summary()
        home = summary.get("home", "n/a")
        config_path = summary.get("resolved_config_path", str(default_config_path()))
        runtime_mode = summary.get("runtime_mode", "n/a")
        active_model = self._active_model_id()
        self._main_screen().query_one("#sidebar-home", Static).update(
            f"[Home]\n{home}"
        )
        self._main_screen().query_one("#sidebar-config", Static).update(
            f"[Config]\n{config_path}"
        )
        self._main_screen().query_one("#sidebar-runtime", Static).update(
            f"[Mode]\n{runtime_mode}\n\n[Active model]\n{active_model}"
        )
        self._main_screen().query_one("#sidebar-context", Static).update(
            f"[Context]\n{self.current_command}\n\n[State]\n"
            f"{'busy' if self.active_task else 'idle'}"
        )

    def _active_model_id(self) -> str:
        try:
            status = execute_command("status")
        except Exception:
            return "n/a"
        if not isinstance(status, dict):
            return "n/a"
        active_model = status.get("active_model")
        if isinstance(active_model, dict):
            model_id = active_model.get("model_id")
            if model_id is not None:
                return str(model_id)
        return "n/a"

    def _format_event(self, event: ExecutionEvent) -> str:
        if event.kind == "runtime_snapshot":
            return self._format_runtime_snapshot(event.payload)
        if event.kind == "alert":
            return self._format_alert(event.payload)
        if event.kind == "status":
            return f"STATUS  {event.message}  {self._format_mapping(event.payload)}"
        if event.kind == "artifact_written":
            return f"SAVED   {event.message}  {self._format_mapping(event.payload)}"
        if event.kind == "summary":
            suffix = self._format_mapping(event.payload)
            return f"SUMMARY {event.message}{'' if not suffix else f'  {suffix}'}"
        if event.kind == "warning":
            return f"WARNING {event.message}"
        if event.kind == "error":
            return f"ERROR   {event.message}"
        if event.kind == "step_completed":
            return f"DONE    {event.message}"
        return f"START   {event.message}"

    def _format_runtime_snapshot(self, payload: dict[str, object]) -> str:
        mode = payload.get("mode", "n/a")
        cycle = payload.get("cycle", "n/a")
        status = payload.get("status", "n/a")
        equity = payload.get("equity_usd", "n/a")
        cash = payload.get("cash_usd", "n/a")
        holdings = payload.get("holdings", {})
        if isinstance(holdings, dict) and holdings:
            holdings_value = ", ".join(
                f"{asset}:{float(quantity):.4f}" for asset, quantity in sorted(holdings.items())
            )
        else:
            holdings_value = "none"
        return (
            f"RUN     mode={mode} cycle={cycle} status={status} "
            f"equity={equity} cash={cash} holdings={holdings_value}"
        )

    def _format_alert(self, payload: dict[str, object]) -> str:
        return (
            f"ALERT   severity={payload.get('severity', 'n/a')} "
            f"class={payload.get('event_class', 'n/a')} "
            f"message={payload.get('message', 'n/a')}"
        )

    def _format_mapping(self, payload: dict[str, object]) -> str:
        flat_parts: list[str] = []
        for key, value in payload.items():
            if isinstance(value, str | int | float | bool) or value is None:
                flat_parts.append(f"{key}={value}")
        if flat_parts:
            return ", ".join(flat_parts)
        return json.dumps(payload, sort_keys=True)
