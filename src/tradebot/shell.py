"""Interactive Textual shell for Tradebot."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import monotonic

from rich.markup import escape as rich_escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
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

TRANSCRIPT_LIMIT = 160
EXIT_CONFIRMATION_WINDOW_SECONDS = 5.0


@dataclass(frozen=True)
class TranscriptEntry:
    """One operator-facing transcript entry."""

    kind: str
    title: str
    lines: tuple[str, ...] = ()
    action_id: int = 0


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
        background: ansi_default;
        color: #111827;
    }

    #command-form {
        width: 96;
        height: 80%;
        border: round #8b5cf6;
        background: ansi_default;
        color: #111827;
        padding: 1 2;
        scrollbar-size: 0 0;
    }

    Input {
        border: round #6d28d9;
        color: #111827;
    }

    Input:focus {
        border: round #a78bfa;
        background-tint: #ddd6fe 10%;
    }

    OptionList,
    SelectionList {
        border: round #6d28d9;
        color: #111827;
        scrollbar-size: 0 0;
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

    #form-actions Button {
        min-width: 18;
        height: 3;
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
                yield Button("Cancel", id="form-cancel", variant="error")

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
        form = self.query_one("#command-form", VerticalScroll)
        form.show_vertical_scrollbar = False
        form.show_horizontal_scrollbar = False
        for option_list in self.query(OptionList):
            option_list.show_vertical_scrollbar = False
            option_list.show_horizontal_scrollbar = False
        for selection_list in self.query(SelectionList):
            selection_list.show_vertical_scrollbar = False
            selection_list.show_horizontal_scrollbar = False
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
    App {
        background: ansi_default;
        color: #111827;
    }

    Screen {
        layout: vertical;
        background: ansi_default;
        color: #111827;
    }

    #brand {
        height: auto;
        content-align: center middle;
        text-align: center;
        border: round #8b5cf6;
        padding: 1 2;
        margin: 1 1 0 1;
        background: ansi_default;
        color: #111827;
    }

    #body {
        height: 1fr;
        margin: 0 1;
        layout: vertical;
        background: ansi_default;
    }

    #transcript {
        height: 1fr;
        border: round #6d28d9;
        padding: 1 2;
        background: ansi_default;
        color: #111827;
        scrollbar-size: 0 0;
    }

    #input-region {
        height: auto;
        margin: 0 1 1 1;
        background: ansi_default;
    }

    #command-input {
        border: round #6d28d9;
        background: ansi_default;
        color: #111827;
    }

    #command-input:focus {
        border: round #a78bfa;
        background-tint: #ddd6fe 10%;
    }

    #command-input > .input--placeholder,
    #command-input > .input--suggestion {
        color: #6b7280;
    }

    #command-suggestions {
        height: 8;
        border: round #6d28d9;
        margin-top: 1;
        background: ansi_default;
        color: #111827;
        scrollbar-size: 0 0;
    }

    #command-suggestions:focus {
        border: round #a78bfa;
    }

    OptionList {
        background: ansi_default;
        color: #111827;
        scrollbar-size: 0 0;
    }

    OptionList > .option-list--option-highlighted {
        color: #312e81;
        background: #ede9fe;
        text-style: bold;
    }

    OptionList:focus > .option-list--option-highlighted {
        color: #312e81;
        background: #ddd6fe;
        text-style: bold;
    }

    OptionList > .option-list--option-hover {
        color: #312e81;
        background: #f5f3ff;
    }

    Input {
        background: ansi_default;
        color: #111827;
    }

    Checkbox {
        background: ansi_default;
        color: #111827;
    }

    SelectionList {
        background: ansi_default;
        color: #111827;
        scrollbar-size: 0 0;
    }

    Static {
        background: ansi_default;
        color: #111827;
    }

    Button {
        background: #5b21b6;
        border: none;
        color: #faf5ff;
        text-style: bold;
    }

    Button:focus {
        background: #6d28d9;
        color: #faf5ff;
        tint: #ffffff 6%;
    }

    Button:hover {
        background: #6d28d9;
        color: #faf5ff;
        tint: #ffffff 6%;
    }

    Button.-success {
        background: #16a34a;
        color: #f0fdf4;
    }

    Button.-success:hover,
    Button.-success:focus {
        background: #15803d;
        color: #f0fdf4;
    }

    Button.-error {
        background: #dc2626;
        color: #fef2f2;
    }

    Button.-error:hover,
    Button.-error:focus {
        background: #b91c1c;
        color: #fef2f2;
    }
    """

    BINDINGS = [
        ("ctrl+c", "confirm_exit", "Exit shell"),
    ]

    def __init__(self) -> None:
        super().__init__(ansi_color=True)
        self.active_token: CancellationToken | None = None
        self.active_task: asyncio.Task[None] | None = None
        self.current_command: str = "idle"
        self._active_action_id: int = 0
        self._latest_action_id: int = 0
        self._context_entry_index: int | None = None
        self._pending_exit_deadline: float | None = None
        self._transcript_entries: list[TranscriptEntry] = []

    def _main_screen(self) -> Screen[object]:
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield Static(
            f"Crypto Trade Bot  v{__version__}\nInteractive operator shell",
            id="brand",
        )
        with Vertical(id="body"):
            yield RichLog(id="transcript", wrap=True, markup=True)
        with Vertical(id="input-region"):
            yield Input(
                placeholder="Type a command like data source, features build, help, clear, or exit",
                id="command-input",
            )
            yield OptionList(id="command-suggestions")

    def on_mount(self) -> None:
        bootstrap_summary = ensure_app_home_initialized()
        transcript = self._main_screen().query_one("#transcript", RichLog)
        suggestions = self._main_screen().query_one("#command-suggestions", OptionList)
        transcript.show_vertical_scrollbar = False
        transcript.show_horizontal_scrollbar = False
        suggestions.show_vertical_scrollbar = False
        suggestions.show_horizontal_scrollbar = False
        suggestions.display = False
        self._refresh_context_entry(resolve_status=True)
        self._append_entry("system", "Crypto Trade Bot shell ready.")
        if bootstrap_summary is not None:
            self._append_entry(
                "system",
                "Created your default Tradebot home.",
                lines=(
                    f"Home: {bootstrap_summary['home']}",
                    "Starter config and environment files are now in place.",
                ),
            )
        self._refresh_command_suggestions("")
        self._main_screen().query_one("#command-input", Input).focus()

    def action_confirm_exit(self) -> None:
        now = monotonic()
        if self._pending_exit_deadline is not None and now <= self._pending_exit_deadline:
            if self.active_token is not None:
                self.active_token.cancel()
            self.exit()
            return
        self._pending_exit_deadline = now + EXIT_CONFIRMATION_WINDOW_SECONDS
        self._append_entry(
            "warning",
            "Press Ctrl+C again to exit the shell.",
            lines=("Repeat the same shortcut within 5 seconds to close Tradebot shell.",),
            action_id=self._active_action_id,
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "command-input":
            return
        self._clear_exit_confirmation()
        self.current_command = event.value.strip() or "idle"
        self._refresh_command_suggestions(event.value)
        self._refresh_context_entry()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command-input":
            return
        if self.active_task is not None:
            self._append_busy_warning()
            return
        self._clear_exit_confirmation()
        text = event.value.strip()
        if not text:
            return
        self._main_screen().query_one("#command-input", Input).value = ""
        self._handle_shell_input(text)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "command-suggestions":
            return
        if self.active_task is not None:
            self._append_busy_warning()
            return
        self._clear_exit_confirmation()
        text = _stringify_prompt(event.option.prompt)
        self._main_screen().query_one("#command-input", Input).value = ""
        self._handle_shell_input(text)

    def _handle_shell_input(self, text: str) -> None:
        normalized = text.strip()
        lowered = normalized.lower()
        if lowered == "help":
            self._record_shell_command("help")
            self._render_help()
            return
        if lowered == "clear":
            self._transcript_entries.clear()
            self._context_entry_index = None
            self._main_screen().query_one("#transcript", RichLog).clear()
            self._refresh_context_entry()
            self._append_entry(
                "system",
                "History cleared.",
                lines=("Type a new command to continue.",),
            )
            return
        if lowered == "exit":
            self.exit()
            return

        action_id = self._record_shell_command(normalized)

        try:
            parsed = parse_shell_command(normalized)
        except Exception as exc:
            self._append_entry(
                "error",
                "That command could not be understood.",
                lines=(str(exc),),
                action_id=action_id,
            )
            return

        if parsed.spec.fields and not parsed.used_inline_arguments:
            self.push_screen(
                CommandFormScreen(parsed.spec, default_form_values(parsed.spec)),
                lambda params: self._run_form_submission(
                    parsed.spec,
                    normalized,
                    params,
                    action_id,
                ),
            )
            return

        self._submit_command(
            CommandSubmission(spec=parsed.spec, params=parsed.params, text=normalized),
            action_id,
        )

    def _run_form_submission(
        self,
        spec: CommandSpec,
        text: str,
        params: dict[str, object] | None,
        action_id: int,
    ) -> None:
        if params is None:
            self._append_entry(
                "warning",
                f"Stopped setting up {' '.join(spec.tokens)}.",
                lines=("The guided form was closed before the command ran.",),
                action_id=action_id,
            )
            return
        self._submit_command(
            CommandSubmission(spec=spec, params=params, text=text),
            action_id,
        )

    def _submit_command(self, submission: CommandSubmission, action_id: int) -> None:
        self.current_command = submission.text
        self._active_action_id = action_id
        self._set_busy(True)
        self.active_token = CancellationToken()
        self.active_task = asyncio.create_task(self._run_submission(submission, action_id))
        self._refresh_context_entry()

    async def _run_submission(self, submission: CommandSubmission, action_id: int) -> None:
        try:
            result = await asyncio.to_thread(
                execute_command,
                submission.spec.id,
                submission.params,
                emitter=lambda event: self.call_from_thread(self._handle_execution_event, event),
                cancellation_token=self.active_token,
            )
            self._render_command_result(submission.spec.id, result, action_id)
        except Exception as exc:
            self._append_entry(
                "error",
                "The command ended with a problem.",
                lines=(str(exc),),
                action_id=action_id,
            )
        finally:
            self.active_task = None
            self.active_token = None
            self._active_action_id = 0
            self.current_command = "idle"
            self._set_busy(False)
            self._refresh_context_entry(resolve_status=True)

    def _handle_execution_event(self, event: ExecutionEvent) -> None:
        entry_kind, title, lines = self._format_event_entry(event)
        self._append_entry(
            entry_kind,
            title,
            lines=tuple(lines),
            action_id=self._active_action_id,
        )

    def _render_command_result(self, command_id: str, result: object, action_id: int) -> None:
        if command_id == "logs_tail" and isinstance(result, list):
            self._append_entry(
                "result",
                "Recent durable logs.",
                lines=tuple(str(line) for line in result),
                action_id=action_id,
            )
            return
        if command_id == "report_list" and isinstance(result, list):
            lines: list[str] = []
            for entry in result[:25]:
                path = entry.get("path", "")
                category = entry.get("category", "")
                lines.append(f"{category}: {path}")
            if len(result) > 25:
                lines.append(f"{len(result) - 25} more entries are available.")
            self._append_entry(
                "result",
                "Saved reports and artifacts.",
                lines=tuple(lines),
                action_id=action_id,
            )
            return
        if isinstance(result, str):
            self._append_entry(
                "result",
                "Command finished.",
                lines=(result,),
                action_id=action_id,
            )
            return
        if isinstance(result, RuntimeRunResult):
            self._append_entry(
                "result",
                "Runtime finished.",
                lines=(
                    f"Mode: {result.mode}",
                    f"Completed cycles: {result.completed_cycles}",
                ),
                action_id=action_id,
            )
            return
        if isinstance(result, dict):
            self._append_entry(
                "result",
                "Command finished.",
                lines=tuple(self._format_mapping_lines(result)),
                action_id=action_id,
            )
            return
        self._append_entry(
            "result",
            "Command finished.",
            lines=tuple(json.dumps(result, indent=2, sort_keys=True).splitlines()),
            action_id=action_id,
        )

    def _render_help(self) -> None:
        lines = ["Available commands:"]
        for spec in all_command_specs():
            lines.append(f"{' '.join(spec.tokens)}: {spec.description}")
        lines.append("Shell commands: help, clear, exit")
        lines.append("Press Ctrl+C twice within 5 seconds to exit the shell.")
        self._append_entry("help", "How to use the shell.", lines=tuple(lines))

    def _refresh_command_suggestions(self, prefix: str) -> None:
        option_list = self._main_screen().query_one("#command-suggestions", OptionList)
        option_list.clear_options()
        normalized = prefix.strip()
        option_list.display = False
        if not normalized:
            return
        matches = command_choices(prefix)
        if not matches:
            return
        option_list.add_options([Option(match, id=match) for match in matches[:12]])
        option_list.display = True

    def _set_busy(self, busy: bool) -> None:
        input_widget = self._main_screen().query_one("#command-input", Input)
        suggestions = self._main_screen().query_one("#command-suggestions", OptionList)
        input_widget.disabled = busy
        suggestions.disabled = busy
        suggestions.display = False if busy else suggestions.display
        if not busy:
            input_widget.focus()

    def _append_busy_warning(self) -> None:
        self._append_entry(
            "warning",
            "Another command is already running.",
            lines=(
                "Wait for it to finish, or press Ctrl+C twice within 5 seconds to exit the shell.",
            ),
            action_id=self._active_action_id,
        )

    def _append_entry(
        self,
        kind: str,
        title: str,
        *,
        lines: tuple[str, ...] = (),
        action_id: int = 0,
    ) -> None:
        entry = TranscriptEntry(kind=kind, title=title, lines=lines, action_id=action_id)
        self._transcript_entries.append(entry)
        self._trim_transcript_entries()
        if action_id > 0:
            self._latest_action_id = action_id
        self._rerender_transcript()

    def _record_shell_command(self, text: str) -> int:
        action_id = self._next_action_id()
        self._append_entry(
            "command",
            "Running command.",
            lines=(text,),
            action_id=action_id,
        )
        return action_id

    def _rerender_transcript(self) -> None:
        transcript = self._main_screen().query_one("#transcript", RichLog)
        transcript.clear()
        visible_entries = self._transcript_entries[-TRANSCRIPT_LIMIT:]
        for index, entry in enumerate(visible_entries):
            title_style, body_style, label = self._entry_display(entry)
            transcript.write(
                f"[{title_style}]{rich_escape(label)}:[/] "
                f"[{title_style}]{rich_escape(entry.title)}[/]"
            )
            for line in entry.lines:
                transcript.write(f"[{body_style}]  {rich_escape(line)}[/]")
            if index != len(visible_entries) - 1:
                transcript.write("")

    def _entry_display(self, entry: TranscriptEntry) -> tuple[str, str, str]:
        is_latest = entry.action_id > 0 and entry.action_id == self._latest_action_id
        if entry.kind == "context":
            return ("bold #374151", "#4b5563", "Context")
        if entry.kind == "command":
            return self._entry_theme(is_latest, "Latest command", "Earlier command")
        if entry.kind == "result":
            return self._entry_theme(is_latest, "Latest result", "Earlier result")
        if entry.kind == "warning":
            return self._entry_theme(is_latest, "Latest note", "Earlier note")
        if entry.kind == "error":
            return self._entry_theme(is_latest, "Latest problem", "Earlier problem")
        if entry.kind == "help":
            return ("bold #1f2937", "#4b5563", "Shell help")
        if entry.kind == "system":
            return ("bold #1f2937", "#4b5563", "Shell")
        return self._entry_theme(is_latest, "Latest update", "Earlier update")

    def _entry_theme(
        self,
        is_latest: bool,
        latest_label: str,
        earlier_label: str,
    ) -> tuple[str, str, str]:
        if is_latest:
            return ("bold #111827", "#374151", latest_label)
        return ("#6b7280", "#4b5563", earlier_label)

    def _next_action_id(self) -> int:
        self._latest_action_id += 1
        return self._latest_action_id

    def _refresh_context_entry(self, *, resolve_status: bool = False) -> None:
        summary = safe_config_summary()
        home = summary.get("home", "n/a")
        config_path = summary.get("resolved_config_path", str(default_config_path()))
        runtime_mode = summary.get("runtime_mode", "n/a")
        self._set_context_entry(
            "Current shell context.",
            lines=(
                f"Home: {home}",
                f"Config: {config_path}",
                f"Runtime: mode={runtime_mode}",
                "Session: "
                f"command={self.current_command} | "
                f"state={'running' if self.active_task else 'idle'}",
            ),
        )

    def _clear_exit_confirmation(self) -> None:
        self._pending_exit_deadline = None

    def _set_context_entry(self, title: str, *, lines: tuple[str, ...]) -> None:
        entry = TranscriptEntry(kind="context", title=title, lines=lines)
        if self._context_entry_index is None or self._context_entry_index >= len(
            self._transcript_entries
        ):
            self._transcript_entries.insert(0, entry)
            self._context_entry_index = 0
        else:
            self._transcript_entries[self._context_entry_index] = entry
        self._trim_transcript_entries()
        self._rerender_transcript()

    def _trim_transcript_entries(self) -> None:
        if len(self._transcript_entries) <= TRANSCRIPT_LIMIT:
            return
        if (
            self._context_entry_index == 0
            and self._transcript_entries
            and self._transcript_entries[0].kind == "context"
        ):
            self._transcript_entries = [
                self._transcript_entries[0],
                *self._transcript_entries[1:][-(TRANSCRIPT_LIMIT - 1) :],
            ]
            self._context_entry_index = 0
            return
        self._transcript_entries = self._transcript_entries[-TRANSCRIPT_LIMIT:]
        self._context_entry_index = None

    def _format_event_entry(self, event: ExecutionEvent) -> tuple[str, str, list[str]]:
        if event.kind == "runtime_snapshot":
            return (
                "update",
                "Runtime cycle completed.",
                self._format_runtime_snapshot_lines(event.payload),
            )
        if event.kind == "alert":
            severity = str(event.payload.get("severity", "notice")).lower()
            kind = "error" if severity in {"error", "critical", "high"} else "warning"
            return (kind, "Trading alert.", self._format_alert_lines(event.payload))
        if event.kind == "status":
            return ("update", event.message, self._format_mapping_lines(event.payload))
        if event.kind == "artifact_written":
            return ("result", event.message, self._format_mapping_lines(event.payload))
        if event.kind == "summary":
            return ("result", event.message, self._format_mapping_lines(event.payload))
        if event.kind == "warning":
            return ("warning", "Attention needed.", [event.message])
        if event.kind == "error":
            return ("error", "Command reported a problem.", [event.message])
        if event.kind == "step_completed":
            return ("result", event.message, self._format_mapping_lines(event.payload))
        return ("update", event.message, self._format_mapping_lines(event.payload))

    def _format_runtime_snapshot_lines(self, payload: dict[str, object]) -> list[str]:
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
        return [
            f"Mode: {mode}",
            f"Cycle: {cycle}",
            f"Status: {status}",
            f"Equity USD: {equity}",
            f"Cash USD: {cash}",
            f"Holdings: {holdings_value}",
        ]

    def _format_alert_lines(self, payload: dict[str, object]) -> list[str]:
        return [
            f"Severity: {payload.get('severity', 'n/a')}",
            f"Class: {payload.get('event_class', 'n/a')}",
            f"Message: {payload.get('message', 'n/a')}",
        ]

    def _format_mapping_lines(
        self,
        payload: dict[str, object],
        *,
        indent: str = "",
    ) -> list[str]:
        lines: list[str] = []
        for key, value in payload.items():
            label = f"{indent}{key.replace('_', ' ').title()}"
            if isinstance(value, dict):
                if not value:
                    lines.append(f"{label}: none")
                    continue
                lines.append(f"{label}:")
                lines.extend(self._format_mapping_lines(value, indent=f"{indent}  "))
                continue
            if isinstance(value, list):
                if not value:
                    lines.append(f"{label}: none")
                    continue
                if all(not isinstance(item, dict | list | tuple | set) for item in value):
                    rendered = ", ".join(self._format_scalar(item) for item in value)
                    lines.append(f"{label}: {rendered}")
                    continue
                lines.append(f"{label}:")
                for item in value[:5]:
                    if isinstance(item, dict):
                        lines.extend(self._format_mapping_lines(item, indent=f"{indent}    "))
                    else:
                        lines.append(f"{indent}  - {self._format_scalar(item)}")
                if len(value) > 5:
                    lines.append(f"{indent}  - {len(value) - 5} more")
                continue
            lines.append(f"{label}: {self._format_scalar(value)}")
        if lines:
            return lines
        return json.dumps(payload, sort_keys=True).splitlines()

    def _format_scalar(self, value: object) -> str:
        if value is None:
            return "none"
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value)
