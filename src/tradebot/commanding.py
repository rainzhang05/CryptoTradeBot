"""Shared command metadata, execution, and parsing for direct CLI and shell usage."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from tradebot import __version__
from tradebot.backtest.service import BacktestService
from tradebot.cancellation import CancellationToken
from tradebot.config import (
    AppConfig,
    ConfigError,
    app_home_layout,
    default_config_path,
    default_tradebot_home,
    ensure_app_home_initialized,
    initialize_app_home,
    load_config,
    sanitized_config_payload,
)
from tradebot.constants import FIXED_UNIVERSE, SUPPORTED_MODES
from tradebot.data.service import DataService
from tradebot.logging_config import configure_logging
from tradebot.model.service import ModelService
from tradebot.operations import OperationsService
from tradebot.research.service import ResearchService
from tradebot.runtime import RuntimeService, RuntimeSnapshot

EventKind = Literal[
    "step_started",
    "step_completed",
    "status",
    "warning",
    "error",
    "runtime_snapshot",
    "alert",
    "artifact_written",
    "summary",
]
ValueType = Literal["string", "int", "bool", "path"]
ChoiceProvider = Callable[[], list[str]]
EventEmitter = Callable[["ExecutionEvent"], None]


@dataclass(frozen=True)
class ExecutionEvent:
    """One normalized command-execution event."""

    kind: EventKind
    message: str
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandFieldSpec:
    """Metadata for one command field used by the shell."""

    name: str
    label: str
    kind: Literal["argument", "option"] = "option"
    flags: tuple[str, ...] = ()
    negative_flags: tuple[str, ...] = ()
    value_type: ValueType = "string"
    required: bool = False
    multiple: bool = False
    default: object | None = None
    help: str = ""
    choices: tuple[str, ...] = ()
    choice_provider: ChoiceProvider | None = None

    def resolved_choices(self) -> list[str]:
        if self.choice_provider is not None:
            return self.choice_provider()
        return list(self.choices)


@dataclass(frozen=True)
class CommandSpec:
    """Shared command description for shell parsing and guided forms."""

    id: str
    tokens: tuple[str, ...]
    description: str
    fields: tuple[CommandFieldSpec, ...] = ()


@dataclass(frozen=True)
class RuntimeRunResult:
    """Result payload for the shared runtime command handler."""

    mode: str
    completed_cycles: int
    snapshots: list[dict[str, object]]


@dataclass(frozen=True)
class ParsedCommand:
    """Parsed shell command line."""

    spec: CommandSpec
    params: dict[str, object]
    used_inline_arguments: bool
    provided_fields: set[str]


def _emit(
    emitter: EventEmitter | None,
    kind: EventKind,
    message: str,
    payload: dict[str, object] | None = None,
) -> None:
    if emitter is None:
        return
    emitter(ExecutionEvent(kind=kind, message=message, payload=payload or {}))


def _command_root() -> Path:
    config_path = default_config_path()
    if config_path.parent.name == "config":
        return config_path.parent.parent.resolve()
    return Path.cwd().resolve()


def _models_dir() -> Path:
    root = _command_root()
    return root / "artifacts" / "models"


def _backtests_dir() -> Path:
    root = _command_root()
    return root / "artifacts" / "backtests"


def _artifacts_dir() -> Path:
    root = _command_root()
    return root / "artifacts"


def _list_model_ids() -> list[str]:
    directory = _models_dir()
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_dir())


def _list_backtest_run_ids() -> list[str]:
    directory = _backtests_dir()
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_dir())


def _list_report_sources() -> list[str]:
    directory = _artifacts_dir()
    if not directory.exists():
        return []
    return sorted(
        str(path.relative_to(_command_root()))
        for path in directory.rglob("*")
        if path.is_file()
    )


def _load_app_config() -> AppConfig:
    ensure_app_home_initialized()
    config = load_config()
    configure_logging(config)
    return config


def _config_root_paths() -> dict[str, str]:
    config_path = default_config_path()
    if config_path.parent.name == "config":
        home = config_path.parent.parent.resolve()
    else:
        home = default_tradebot_home()
    layout = app_home_layout(home)
    payload = layout.to_dict()
    payload["resolved_config_path"] = str(config_path)
    return payload


def _check_cancel(token: CancellationToken | None) -> None:
    if token is not None:
        token.raise_if_cancelled()


def render_runtime_snapshot(snapshot: RuntimeSnapshot) -> str:
    """Render one runtime-cycle summary for terminal monitoring output."""
    timestamp = "n/a" if snapshot.timestamp is None else str(snapshot.timestamp)
    equity = "n/a" if snapshot.equity_usd is None else f"{snapshot.equity_usd:.2f}"
    cash = "n/a" if snapshot.cash_usd is None else f"{snapshot.cash_usd:.2f}"
    holdings = ", ".join(
        f"{asset}:{quantity:.8f}"
        for asset, quantity in sorted(snapshot.holdings.items())
    )
    incidents = ", ".join(snapshot.incidents)
    drawdown = (
        "n/a"
        if snapshot.portfolio_drawdown is None
        else f"{snapshot.portfolio_drawdown:.2%}"
    )
    return " | ".join(
        [
            f"mode={snapshot.mode}",
            f"cycle={snapshot.cycle}",
            f"status={snapshot.status}",
            f"system={snapshot.system_status}",
            f"connectivity={snapshot.connectivity_state}",
            f"timestamp={timestamp}",
            f"regime={snapshot.regime_state or 'n/a'}",
            f"risk={snapshot.risk_state or 'n/a'}",
            f"drawdown={drawdown}",
            f"equity_usd={equity}",
            f"cash_usd={cash}",
            f"holdings={holdings or 'none'}",
            f"fills={snapshot.fill_count}",
            f"recent_fills={_render_fill_summary(snapshot)}",
            f"open_orders={snapshot.open_order_count}",
            f"model={snapshot.model_id or 'n/a'}",
            f"model_summary={_render_model_summary(snapshot)}",
            f"decision_executed={'yes' if snapshot.decision_executed else 'no'}",
            f"freeze={snapshot.freeze_reason or 'none'}",
            f"incidents={incidents or 'none'}",
        ]
    )


def render_alert_event(alert: Any) -> str:
    """Render one alert event for terminal display."""
    return " | ".join(
        [
            "ALERT",
            f"severity={alert.severity}",
            f"class={alert.event_class}",
            f"mode={alert.mode}",
            f"message={alert.message}",
            f"email={'sent' if alert.email_sent else alert.email_error or 'not_sent'}",
        ]
    )


def _render_fill_summary(snapshot: RuntimeSnapshot) -> str:
    fills = getattr(snapshot, "fills", [])
    if not fills:
        return "none"
    rendered = []
    for fill in fills[:3]:
        asset = str(fill.get("asset", "?"))
        side = str(fill.get("side", "?"))
        quantity = float(fill.get("quantity", 0.0))
        rendered.append(f"{asset}:{side}:{quantity:.6f}")
    return ",".join(rendered)


def _render_model_summary(snapshot: RuntimeSnapshot) -> str:
    predictions = getattr(snapshot, "predictions", {})
    if not predictions:
        return "n/a"
    ranked = sorted(
        predictions.items(),
        key=lambda item: float(item[1].get("expected_return_score", 0.0)),
        reverse=True,
    )
    top_asset, top_scores = ranked[0]
    high_downside = sum(
        1
        for scores in predictions.values()
        if float(scores.get("downside_risk_score", 0.0)) >= 0.55
    )
    high_sell_risk = sum(
        1
        for scores in predictions.values()
        if float(scores.get("sell_risk_score", 0.0)) >= 0.55
    )
    return (
        f"top={top_asset}:{float(top_scores.get('expected_return_score', 0.0)):.3f},"
        f"downside_flags={high_downside},sell_flags={high_sell_risk}"
    )


def handle_version(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> str:
    del params
    _check_cancel(cancellation_token)
    _emit(emitter, "summary", "Version loaded.", {"version": __version__})
    return __version__


def handle_config_path(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> str:
    del params
    _check_cancel(cancellation_token)
    config_path = str(default_config_path())
    _emit(emitter, "summary", "Resolved configuration path.", {"config_path": config_path})
    return config_path


def handle_init(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    home = params.get("home")
    force = bool(params.get("force", False))
    _check_cancel(cancellation_token)
    _emit(emitter, "step_started", "Bootstrapping Tradebot home.")
    summary = initialize_app_home(
        home=(None if home in {None, ""} else Path(str(home))),
        force=force,
    )
    _emit(emitter, "step_completed", "Tradebot home is ready.", summary)
    return summary


def handle_doctor(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    del params
    _check_cancel(cancellation_token)
    _emit(emitter, "step_started", "Running environment and exchange checks.")
    config = _load_app_config()
    summary = cast(dict[str, object], OperationsService(config).doctor_summary())
    _emit(emitter, "summary", "Doctor checks finished.", summary)
    return summary


def handle_config_show(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    del params
    _check_cancel(cancellation_token)
    config = _load_app_config()
    payload = sanitized_config_payload(config)
    _emit(emitter, "summary", "Loaded active configuration.", payload)
    return payload


def handle_config_validate(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> str:
    del params
    _check_cancel(cancellation_token)
    config = _load_app_config()
    message = f"Configuration valid: {config.config_path}"
    _emit(emitter, "summary", message, {"config_path": str(config.config_path)})
    return message


def handle_run(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> RuntimeRunResult:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    effective_mode = str(params.get("mode") or config.runtime.default_mode)
    max_cycles = params.get("max_cycles")
    cycle_limit = None if max_cycles in {None, ""} else int(str(max_cycles))
    _emit(
        emitter,
        "step_started",
        "Starting shared runtime.",
        {"mode": effective_mode, "max_cycles": cycle_limit},
    )
    runtime = RuntimeService(config)
    snapshots = runtime.run(
        mode=effective_mode,
        max_cycles=cycle_limit,
        cancellation_token=cancellation_token,
        on_cycle=lambda snapshot: _emit(
            emitter,
            "runtime_snapshot",
            "Runtime cycle completed.",
            snapshot.to_dict(),
        ),
        on_alert=lambda alert: _emit(
            emitter,
            "alert",
            "Runtime alert emitted.",
            alert.to_dict(),
        ),
    )
    result = RuntimeRunResult(
        mode=effective_mode,
        completed_cycles=len(snapshots),
        snapshots=[snapshot.to_dict() for snapshot in snapshots],
    )
    _emit(
        emitter,
        "summary",
        "Runtime finished.",
        {"mode": effective_mode, "completed_cycles": len(snapshots)},
    )
    return result


def handle_stop(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    del params
    _check_cancel(cancellation_token)
    config = _load_app_config()
    summary = cast(dict[str, object], OperationsService(config).stop_runtime())
    _emit(emitter, "summary", "Termination requested.", summary)
    return summary


def handle_status(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    del params
    _check_cancel(cancellation_token)
    config = _load_app_config()
    summary = cast(dict[str, object], OperationsService(config).runtime_status())
    _emit(emitter, "summary", "Loaded runtime status.", summary)
    return summary


def handle_email_set(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    recipient = str(params["recipient"])
    config = _load_app_config()
    summary = cast(dict[str, object], OperationsService(config).set_email_recipient(recipient))
    _emit(emitter, "summary", "Updated alert recipient.", summary)
    return summary


def handle_email_test(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    recipient = params.get("recipient")
    config = _load_app_config()
    summary = cast(
        dict[str, object],
        OperationsService(config).send_test_email(
            recipient=(None if recipient in {None, ""} else str(recipient))
        ),
    )
    _emit(emitter, "summary", "Test email completed.", summary)
    return summary


def handle_report_list(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> list[dict[str, object]]:
    del params
    _check_cancel(cancellation_token)
    config = _load_app_config()
    summary = cast(list[dict[str, object]], OperationsService(config).list_reports())
    _emit(emitter, "summary", "Loaded stored reports.", {"count": len(summary)})
    return summary


def handle_report_export(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    summary = cast(
        dict[str, object],
        OperationsService(config).export_report(
            str(params["source"]),
            Path(str(params["destination"])),
        ),
    )
    _emit(emitter, "artifact_written", "Exported artifact.", summary)
    return summary


def handle_logs_tail(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> list[str]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    lines = int(str(params.get("lines", 50)))
    rendered_lines = cast(list[str], OperationsService(config).tail_logs(lines=lines))
    _emit(emitter, "summary", "Rendered durable log tail.", {"lines": len(rendered_lines)})
    return rendered_lines


def handle_data_import(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    assets = _tuple_or_none(params.get("assets"))
    _emit(emitter, "step_started", "Importing Kraken raw data.", {"assets": list(assets or ())})
    summary = DataService(config).import_kraken_raw(assets=assets).to_dict()
    _emit(emitter, "summary", "Data import finished.", summary)
    return summary


def handle_data_check(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    assets = _tuple_or_none(params.get("assets"))
    summary = DataService(config).check_canonical(assets=assets).to_dict()
    _emit(emitter, "summary", "Canonical integrity check finished.", summary)
    return summary


def handle_data_source(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    del params
    _check_cancel(cancellation_token)
    config = _load_app_config()
    summary = DataService(config).source_summary()
    _emit(emitter, "summary", "Loaded source coverage.", summary)
    return summary


def handle_data_sync(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    assets = _tuple_or_none(params.get("assets"))
    summary = DataService(config).sync_canonical(assets=assets)
    _emit(emitter, "summary", "Canonical sync finished.", summary)
    return summary


def handle_data_complete(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    assets = _tuple_or_none(params.get("assets"))
    allow_synthetic = bool(params.get("allow_synthetic", True))
    _emit(
        emitter,
        "step_started",
        "Completing canonical data.",
        {"assets": list(assets or ()), "allow_synthetic": allow_synthetic},
    )
    summary = DataService(config).complete_canonical(
        assets=assets,
        allow_synthetic=allow_synthetic,
        cancellation_token=cancellation_token,
        progress_callback=(
            None
            if emitter is None
            else lambda payload: _emit(emitter, "status", "Data completion progress.", payload)
        ),
    )
    _emit(emitter, "summary", "Canonical completion finished.", summary)
    return summary


def handle_data_prune_raw(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    del params
    _check_cancel(cancellation_token)
    config = _load_app_config()
    summary = DataService(config).prune_raw_kraken()
    _emit(emitter, "summary", "Pruned unsupported raw files.", summary)
    return summary


def handle_features_build(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    assets = _tuple_or_none(params.get("assets"))
    force = bool(params.get("force", False))
    _emit(emitter, "step_started", "Building deterministic feature store.")
    summary = ResearchService(config).build_feature_store(
        assets=assets,
        force=force,
        cancellation_token=cancellation_token,
    ).to_dict()
    _emit(emitter, "summary", "Feature build finished.", summary)
    return summary


def handle_model_train(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    assets = _tuple_or_none(params.get("assets"))
    force_features = bool(params.get("force_features", False))
    _emit(emitter, "step_started", "Training model artifact.")
    summary = ModelService(config).train_model(
        assets=assets,
        force_features=force_features,
        cancellation_token=cancellation_token,
        progress_callback=(
            None
            if emitter is None
            else lambda payload: _emit(emitter, "status", "Model training progress.", payload)
        ),
    ).to_dict()
    _emit(emitter, "summary", "Model training finished.", summary)
    return summary


def handle_model_validate(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    model_id = params.get("model_id")
    summary = ModelService(config).validate_model(
        model_id=(None if model_id in {None, ""} else str(model_id))
    ).to_dict()
    _emit(emitter, "summary", "Model validation finished.", summary)
    return summary


def handle_model_promote(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    model_id = params.get("model_id")
    summary = ModelService(config).promote_model(
        model_id=(None if model_id in {None, ""} else str(model_id))
    ).to_dict()
    _emit(emitter, "summary", "Model promoted.", summary)
    return summary


def handle_backtest_run(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    assets = _tuple_or_none(params.get("assets"))
    force_features = bool(params.get("force_features", False))
    _emit(emitter, "step_started", "Running backtest.")
    summary = BacktestService(config).run_backtest(
        assets=assets,
        force_features=force_features,
        cancellation_token=cancellation_token,
        progress_callback=(
            None
            if emitter is None
            else lambda payload: _emit(emitter, "status", "Backtest progress.", payload)
        ),
    ).to_dict()
    _emit(emitter, "summary", "Backtest finished.", summary)
    return summary


def handle_backtest_report(
    params: dict[str, object],
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, object]:
    _check_cancel(cancellation_token)
    config = _load_app_config()
    run_id = params.get("run_id")
    summary = BacktestService(config).load_backtest_report(
        run_id=(None if run_id in {None, ""} else str(run_id))
    )
    _emit(emitter, "summary", "Loaded backtest report.", summary)
    return summary


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("version", ("version",), "Print the current application version."),
    CommandSpec("config_path", ("config-path",), "Print the resolved configuration path."),
    CommandSpec(
        "init",
        ("init",),
        "Bootstrap the default application home and starter files.",
        fields=(
            CommandFieldSpec(
                name="home",
                label="Application home",
                flags=("--home",),
                value_type="path",
                help="Optional application-home override.",
            ),
            CommandFieldSpec(
                name="force",
                label="Overwrite starter files",
                flags=("--force",),
                value_type="bool",
                default=False,
                help="Rewrite starter config and env files when they already exist.",
            ),
        ),
    ),
    CommandSpec(
        "doctor",
        ("doctor",),
        "Validate config, local environment, and exchange connectivity.",
    ),
    CommandSpec("config_show", ("config", "show"), "Print the active non-secret configuration."),
    CommandSpec(
        "config_validate",
        ("config", "validate"),
        "Validate the active configuration and print a short success message.",
    ),
    CommandSpec(
        "run",
        ("run",),
        "Start the shared simulate or live runtime loop.",
        fields=(
            CommandFieldSpec(
                name="mode",
                label="Runtime mode",
                flags=("--mode",),
                choices=SUPPORTED_MODES,
                help="Runtime mode to execute.",
            ),
            CommandFieldSpec(
                name="max_cycles",
                label="Max cycles",
                flags=("--max-cycles",),
                value_type="int",
                help="Optional cycle count override.",
            ),
        ),
    ),
    CommandSpec("stop", ("stop",), "Stop a managed runtime process when one is active."),
    CommandSpec("status", ("status",), "Show the latest known runtime status."),
    CommandSpec(
        "email_set",
        ("email", "set"),
        "Set or update the configured alert email recipient.",
        fields=(
            CommandFieldSpec(
                name="recipient",
                label="Recipient",
                kind="argument",
                required=True,
                help="Alert email recipient.",
            ),
        ),
    ),
    CommandSpec(
        "email_test",
        ("email", "test"),
        "Send a test email using the configured SMTP settings.",
        fields=(
            CommandFieldSpec(
                name="recipient",
                label="Recipient override",
                flags=("--recipient",),
                help="Optional override recipient.",
            ),
        ),
    ),
    CommandSpec("report_list", ("report", "list"), "List stored reports and artifacts."),
    CommandSpec(
        "report_export",
        ("report", "export"),
        "Export one stored report or artifact to a chosen destination.",
        fields=(
            CommandFieldSpec(
                name="source",
                label="Source report",
                kind="argument",
                required=True,
                choice_provider=_list_report_sources,
            ),
            CommandFieldSpec(
                name="destination",
                label="Destination path",
                kind="argument",
                required=True,
                value_type="path",
            ),
        ),
    ),
    CommandSpec(
        "logs_tail",
        ("logs", "tail"),
        "Tail recent durable logs in a readable format.",
        fields=(
            CommandFieldSpec(
                name="lines",
                label="Line count",
                flags=("--lines",),
                value_type="int",
                default=50,
            ),
        ),
    ),
    CommandSpec(
        "data_import",
        ("data", "import"),
        "Import raw Kraken trade files into canonical candles.",
        fields=(
            CommandFieldSpec(
                name="assets",
                label="Assets",
                flags=("--assets",),
                multiple=True,
                choice_provider=lambda: list(FIXED_UNIVERSE),
                help="Assets to import. Leave blank for all supported assets.",
            ),
        ),
    ),
    CommandSpec(
        "data_check",
        ("data", "check"),
        "Validate canonical Kraken candles and emit an integrity report.",
        fields=(
            CommandFieldSpec(
                name="assets",
                label="Assets",
                flags=("--assets",),
                multiple=True,
                choice_provider=lambda: list(FIXED_UNIVERSE),
            ),
        ),
    ),
    CommandSpec("data_source", ("data", "source"), "Show raw and canonical source coverage."),
    CommandSpec(
        "data_sync",
        ("data", "sync"),
        "Extend canonical candles using public exchange APIs.",
        fields=(
            CommandFieldSpec(
                name="assets",
                label="Assets",
                flags=("--assets",),
                multiple=True,
                choice_provider=lambda: list(FIXED_UNIVERSE),
            ),
        ),
    ),
    CommandSpec(
        "data_complete",
        ("data", "complete"),
        "Fill canonical gaps and extend all selected series to the latest closed interval.",
        fields=(
            CommandFieldSpec(
                name="assets",
                label="Assets",
                flags=("--assets",),
                multiple=True,
                choice_provider=lambda: list(FIXED_UNIVERSE),
            ),
            CommandFieldSpec(
                name="allow_synthetic",
                label="Allow synthetic fill",
                flags=("--allow-synthetic",),
                negative_flags=("--no-allow-synthetic",),
                value_type="bool",
                default=True,
            ),
        ),
    ),
    CommandSpec("data_prune_raw", ("data", "prune-raw"), "Delete unsupported raw Kraken files."),
    CommandSpec(
        "features_build",
        ("features", "build"),
        "Build a deterministic feature and label dataset.",
        fields=(
            CommandFieldSpec(
                name="assets",
                label="Assets",
                flags=("--assets",),
                multiple=True,
                choice_provider=lambda: list(FIXED_UNIVERSE),
            ),
            CommandFieldSpec(
                name="force",
                label="Force rebuild",
                flags=("--force",),
                value_type="bool",
                default=False,
            ),
        ),
    ),
    CommandSpec(
        "model_train",
        ("model", "train"),
        "Train the Phase 6 ML artifact with walk-forward validation.",
        fields=(
            CommandFieldSpec(
                name="assets",
                label="Assets",
                flags=("--assets",),
                multiple=True,
                choice_provider=lambda: list(FIXED_UNIVERSE),
            ),
            CommandFieldSpec(
                name="force_features",
                label="Force feature rebuild",
                flags=("--force-features",),
                value_type="bool",
                default=False,
            ),
        ),
    ),
    CommandSpec(
        "model_validate",
        ("model", "validate"),
        "Validate one trained model artifact against the promotion rules.",
        fields=(
            CommandFieldSpec(
                name="model_id",
                label="Model id",
                flags=("--model-id",),
                choice_provider=_list_model_ids,
            ),
        ),
    ),
    CommandSpec(
        "model_promote",
        ("model", "promote"),
        "Promote one validated model artifact to the active strategy pointer.",
        fields=(
            CommandFieldSpec(
                name="model_id",
                label="Model id",
                flags=("--model-id",),
                choice_provider=_list_model_ids,
            ),
        ),
    ),
    CommandSpec(
        "backtest_run",
        ("backtest", "run"),
        "Execute a reproducible Kraken-only backtest on canonical daily data.",
        fields=(
            CommandFieldSpec(
                name="assets",
                label="Assets",
                flags=("--assets",),
                multiple=True,
                choice_provider=lambda: list(FIXED_UNIVERSE),
            ),
            CommandFieldSpec(
                name="force_features",
                label="Force feature rebuild",
                flags=("--force-features",),
                value_type="bool",
                default=False,
            ),
        ),
    ),
    CommandSpec(
        "backtest_report",
        ("backtest", "report"),
        "Print a stored backtest report.",
        fields=(
            CommandFieldSpec(
                name="run_id",
                label="Run id",
                flags=("--run-id",),
                choice_provider=_list_backtest_run_ids,
            ),
        ),
    ),
)


COMMAND_HANDLERS: dict[str, Callable[..., object]] = {
    "version": handle_version,
    "config_path": handle_config_path,
    "init": handle_init,
    "doctor": handle_doctor,
    "config_show": handle_config_show,
    "config_validate": handle_config_validate,
    "run": handle_run,
    "stop": handle_stop,
    "status": handle_status,
    "email_set": handle_email_set,
    "email_test": handle_email_test,
    "report_list": handle_report_list,
    "report_export": handle_report_export,
    "logs_tail": handle_logs_tail,
    "data_import": handle_data_import,
    "data_check": handle_data_check,
    "data_source": handle_data_source,
    "data_sync": handle_data_sync,
    "data_complete": handle_data_complete,
    "data_prune_raw": handle_data_prune_raw,
    "features_build": handle_features_build,
    "model_train": handle_model_train,
    "model_validate": handle_model_validate,
    "model_promote": handle_model_promote,
    "backtest_run": handle_backtest_run,
    "backtest_report": handle_backtest_report,
}


def all_command_specs() -> tuple[CommandSpec, ...]:
    """Return the full set of shared direct-command specs."""
    return COMMAND_SPECS


def command_choices(prefix: str = "") -> list[str]:
    """Return shell command suggestions filtered by prefix."""
    normalized = prefix.strip().lower()
    choices = [" ".join(spec.tokens) for spec in COMMAND_SPECS]
    if not normalized:
        return choices
    return [choice for choice in choices if choice.lower().startswith(normalized)]


def command_spec_by_id(command_id: str) -> CommandSpec:
    for spec in COMMAND_SPECS:
        if spec.id == command_id:
            return spec
    raise KeyError(f"Unknown command id: {command_id}")


def execute_command(
    command_id: str,
    params: dict[str, object] | None = None,
    *,
    emitter: EventEmitter | None = None,
    cancellation_token: CancellationToken | None = None,
) -> object:
    """Execute one shared command handler."""
    handler = COMMAND_HANDLERS[command_id]
    return handler(
        params or {},
        emitter=emitter,
        cancellation_token=cancellation_token,
    )


def render_direct_output(command_id: str, payload: object) -> str:
    """Render direct CLI output while preserving the current machine-usable shape."""
    if command_id in {"version", "config_path", "config_validate"}:
        return str(payload)
    if command_id == "logs_tail":
        if not isinstance(payload, list):
            raise TypeError("logs_tail command did not return a list of lines")
        return "\n".join(str(line) for line in payload)
    if command_id == "run":
        runtime_result = payload
        if not isinstance(runtime_result, RuntimeRunResult):
            raise TypeError("run command did not return RuntimeRunResult")
        return (
            f"Completed {runtime_result.completed_cycles} cycle(s) in {runtime_result.mode} mode."
        )
    return json.dumps(payload, indent=2, sort_keys=True)


def parse_shell_command(text: str) -> ParsedCommand:
    """Parse a shell command line into a shared command spec and typed params."""
    tokens = shlex.split(text)
    if not tokens:
        raise ValueError("Enter a command.")

    spec = _match_command_spec(tokens)
    if spec is None:
        raise ValueError(f"Unknown command: {text}")

    remaining = tokens[len(spec.tokens) :]
    params, provided_fields, used_inline_arguments = _parse_remaining_tokens(spec, remaining)
    return ParsedCommand(
        spec=spec,
        params=params,
        used_inline_arguments=used_inline_arguments,
        provided_fields=provided_fields,
    )


def default_form_values(spec: CommandSpec) -> dict[str, object]:
    """Return default shell-form values for the given command."""
    values: dict[str, object] = {}
    for field_spec in spec.fields:
        if field_spec.multiple:
            values[field_spec.name] = []
        else:
            values[field_spec.name] = field_spec.default
    return values


def _match_command_spec(tokens: list[str]) -> CommandSpec | None:
    matches = [
        spec
        for spec in COMMAND_SPECS
        if len(tokens) >= len(spec.tokens)
        and tuple(token.lower() for token in tokens[: len(spec.tokens)]) == spec.tokens
    ]
    if not matches:
        return None
    return max(matches, key=lambda spec: len(spec.tokens))


def _parse_remaining_tokens(
    spec: CommandSpec,
    remaining: list[str],
) -> tuple[dict[str, object], set[str], bool]:
    params = default_form_values(spec)
    provided_fields: set[str] = set()
    used_inline_arguments = bool(remaining)

    option_flags: dict[str, CommandFieldSpec] = {}
    negative_flags: dict[str, CommandFieldSpec] = {}
    positional_fields = [field_spec for field_spec in spec.fields if field_spec.kind == "argument"]
    for field_spec in spec.fields:
        for flag in field_spec.flags:
            option_flags[flag] = field_spec
        for flag in field_spec.negative_flags:
            negative_flags[flag] = field_spec

    positional_index = 0
    index = 0
    while index < len(remaining):
        token = remaining[index]
        if token.startswith("--"):
            flag, inline_value = _split_option_token(token)
            if flag in negative_flags:
                field_spec = negative_flags[flag]
                params[field_spec.name] = False
                provided_fields.add(field_spec.name)
                index += 1
                continue
            option_field = option_flags.get(flag)
            if option_field is None:
                raise ValueError(f"Unknown option for {' '.join(spec.tokens)}: {flag}")
            if option_field.value_type == "bool":
                value = True if inline_value is None else _parse_value(option_field, inline_value)
            else:
                if inline_value is None:
                    index += 1
                    if index >= len(remaining):
                        raise ValueError(f"Option {flag} requires a value")
                    inline_value = remaining[index]
                value = _parse_value(option_field, inline_value)
            if option_field.multiple:
                existing = params.get(option_field.name)
                values = list(existing) if isinstance(existing, list) else []
                values.append(value)
                params[option_field.name] = values
            else:
                params[option_field.name] = value
            provided_fields.add(option_field.name)
            index += 1
            continue

        if positional_index >= len(positional_fields):
            raise ValueError(f"Unexpected argument for {' '.join(spec.tokens)}: {token}")
        field_spec = positional_fields[positional_index]
        value = _parse_value(field_spec, token)
        params[field_spec.name] = value
        provided_fields.add(field_spec.name)
        positional_index += 1
        index += 1

    for field_spec in spec.fields:
        if field_spec.required and params.get(field_spec.name) in {None, "", []}:
            raise ValueError(f"Missing required field: {field_spec.label}")
    return params, provided_fields, used_inline_arguments


def _split_option_token(token: str) -> tuple[str, str | None]:
    if "=" not in token:
        return token, None
    flag, value = token.split("=", 1)
    return flag, value


def _parse_value(field_spec: CommandFieldSpec, raw_value: str) -> object:
    if field_spec.value_type == "int":
        return int(raw_value)
    if field_spec.value_type == "bool":
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value for {field_spec.label}: {raw_value}")
    if field_spec.value_type == "path":
        return raw_value
    return raw_value


def _tuple_or_none(value: object) -> tuple[str, ...] | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, tuple):
        return tuple(str(entry) for entry in value)
    if isinstance(value, list):
        return tuple(str(entry) for entry in value)
    return (str(value),)


def safe_config_summary() -> dict[str, str]:
    """Return shell-friendly home/config context without requiring a valid config file."""
    try:
        config = _load_app_config()
    except ConfigError:
        return _config_root_paths()
    paths = config.resolved_paths()
    return {
        **_config_root_paths(),
        "runtime_mode": config.runtime.default_mode,
        "active_logs_dir": str(paths.logs_dir),
        "active_state_dir": str(paths.state_dir),
        "active_artifacts_dir": str(paths.artifacts_dir),
    }
