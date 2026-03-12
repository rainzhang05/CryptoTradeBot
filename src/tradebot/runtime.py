"""Runtime orchestration for shared simulate and live execution."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import sleep as default_sleep

from tradebot.backtest.service import BacktestService
from tradebot.cancellation import CancellationToken, CommandCancelledError
from tradebot.config import AppConfig, sanitized_config_payload
from tradebot.constants import SUPPORTED_MODES
from tradebot.data.storage import write_json
from tradebot.execution.service import LiveExecutionService
from tradebot.logging_config import get_logger
from tradebot.operations.alerts import AlertEvent, RuntimeAlertService
from tradebot.operations.storage import latest_runtime_context_report_file, runtime_context_file


@dataclass(frozen=True)
class RuntimeSnapshot:
    """A single runtime cycle result."""

    mode: str
    cycle: int
    status: str
    system_status: str = "n/a"
    connectivity_state: str = "n/a"
    timestamp: int | None = None
    regime_state: str | None = None
    risk_state: str | None = None
    equity_usd: float | None = None
    cash_usd: float | None = None
    fill_count: int = 0
    holdings: dict[str, float] = field(default_factory=dict)
    open_order_count: int = 0
    incidents: list[str] = field(default_factory=list)
    freeze_reason: str | None = None
    decision_executed: bool = False
    fills: list[dict[str, object]] = field(default_factory=list)
    portfolio_drawdown: float | None = None
    target_weights: dict[str, float] = field(default_factory=dict)
    decision_actions: dict[str, str] = field(default_factory=dict)
    decision_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a machine-friendly runtime snapshot payload."""
        return asdict(self)


@dataclass(frozen=True)
class RuntimeContextState:
    """Persisted runtime context for restart-safe diagnostics and recovery."""

    pid: int
    mode: str
    status: str
    cycle_limit: int | None
    completed_cycles: int
    started_at: str
    updated_at: str
    config_path: str
    config_snapshot: dict[str, object]
    finished_at: str | None = None
    last_snapshot: dict[str, object] | None = None
    last_alerts: list[dict[str, object]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeProcessState:
    """Persisted metadata for one managed runtime process."""

    pid: int
    mode: str
    started_at: str
    config_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def runtime_process_file(root: Path) -> Path:
    """Return the tracked runtime-process metadata path."""
    return root / "runtime_process.json"


def pid_is_running(pid: int) -> bool:
    """Return whether the given process id is currently active."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RuntimeService:
    """Shared runtime loop for simulate and live mode execution."""

    def __init__(
        self,
        config: AppConfig,
        *,
        backtest_service: BacktestService | None = None,
        live_service: LiveExecutionService | None = None,
        alert_service: RuntimeAlertService | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.logger = get_logger("tradebot.runtime")
        self.backtest_service = backtest_service or BacktestService(config)
        self.live_service = live_service or LiveExecutionService(config)
        self.alert_service = alert_service or RuntimeAlertService(config)
        self.sleep_fn = sleep_fn or default_sleep

    def bootstrap(self) -> None:
        """Ensure the runtime filesystem layout exists."""
        paths = self.config.resolved_paths()
        for directory in (
            paths.data_dir,
            paths.artifacts_dir,
            paths.features_dir,
            paths.experiments_dir,
            paths.artifacts_dir / "reports" / "runtime",
            paths.logs_dir,
            paths.state_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        mode: str,
        max_cycles: int | None = None,
        *,
        dataset_track: str | None = None,
        cancellation_token: CancellationToken | None = None,
        on_cycle: Callable[[RuntimeSnapshot], None] | None = None,
        on_alert: Callable[[AlertEvent], None] | None = None,
    ) -> list[RuntimeSnapshot]:
        """Execute a bounded runtime loop for the requested mode."""
        self.bootstrap()
        cycle_limit = max_cycles if max_cycles is not None else self.config.runtime.max_cycles
        process_path = runtime_process_file(self.config.resolved_paths().state_dir)
        started_at = self._now_iso()
        snapshots: list[RuntimeSnapshot] = []
        latest_alert_payloads: list[dict[str, object]] = []
        try:
            if mode not in SUPPORTED_MODES:
                supported = ", ".join(SUPPORTED_MODES)
                raise ValueError(f"Unsupported mode '{mode}'. Expected one of: {supported}")
            if mode == "live" and (
                not self.config.secrets.kraken_api_key or not self.config.secrets.kraken_api_secret
            ):
                raise ValueError("Live mode requires Kraken API key and secret in the environment")

            self._register_process(process_path, mode, started_at=started_at)
            self._write_runtime_context(
                mode=mode,
                status="starting",
                cycle_limit=cycle_limit,
                completed_cycles=0,
                started_at=started_at,
            )
            self.logger.info("runtime started", extra={"mode": mode, "cycle_limit": cycle_limit})

            cycle = 0
            while cycle_limit is None or cycle < cycle_limit:
                cycle += 1
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                snapshot = self._run_cycle(
                    mode=mode,
                    cycle=cycle,
                    dataset_track=dataset_track,
                )
                alerts = self.alert_service.process_snapshot(snapshot)
                latest_alert_payloads = [alert.to_dict() for alert in alerts]
                self.logger.info(
                    "runtime cycle completed",
                    extra={
                        "mode": mode,
                        "cycle": cycle,
                        "status": snapshot.status,
                        "fill_count": snapshot.fill_count,
                        "freeze_reason": snapshot.freeze_reason,
                    },
                )
                self._write_runtime_context(
                    mode=mode,
                    status="running",
                    cycle_limit=cycle_limit,
                    completed_cycles=cycle,
                    started_at=started_at,
                    last_snapshot=snapshot.to_dict(),
                    last_alerts=latest_alert_payloads,
                )
                if on_cycle is not None:
                    on_cycle(snapshot)
                if on_alert is not None:
                    for alert in alerts:
                        on_alert(alert)
                snapshots.append(snapshot)
                if cycle_limit is None or cycle < cycle_limit:
                    if cancellation_token is not None:
                        cancellation_token.raise_if_cancelled()
                    self.sleep_fn(self.config.runtime.cycle_interval_seconds)

            self.logger.info(
                "runtime finished",
                extra={"mode": mode, "completed_cycles": len(snapshots)},
            )
            self._write_runtime_context(
                mode=mode,
                status="finished",
                cycle_limit=cycle_limit,
                completed_cycles=len(snapshots),
                started_at=started_at,
                finished_at=self._now_iso(),
                last_snapshot=(None if not snapshots else snapshots[-1].to_dict()),
                last_alerts=latest_alert_payloads,
            )
            return snapshots
        except CommandCancelledError:
            self.logger.info(
                "runtime cancelled",
                extra={"mode": mode, "completed_cycles": len(snapshots)},
            )
            self._write_runtime_context(
                mode=mode,
                status="cancelled",
                cycle_limit=cycle_limit,
                completed_cycles=len(snapshots),
                started_at=started_at,
                finished_at=self._now_iso(),
                last_snapshot=(None if not snapshots else snapshots[-1].to_dict()),
                last_alerts=latest_alert_payloads,
                error="Command cancelled",
            )
            raise
        except Exception as exc:
            failure_status = "startup_failed" if not snapshots else "failed"
            self.logger.exception(
                "runtime failed",
                extra={"mode": mode, "completed_cycles": len(snapshots)},
            )
            alerts = (
                self.alert_service.process_startup_failure(mode=mode, error=str(exc))
                if not snapshots
                else []
            )
            latest_alert_payloads = [alert.to_dict() for alert in alerts]
            if on_alert is not None:
                for alert in alerts:
                    on_alert(alert)
            self._write_runtime_context(
                mode=mode,
                status=failure_status,
                cycle_limit=cycle_limit,
                completed_cycles=len(snapshots),
                started_at=started_at,
                finished_at=self._now_iso(),
                last_snapshot=(None if not snapshots else snapshots[-1].to_dict()),
                last_alerts=latest_alert_payloads,
                error=str(exc),
            )
            raise
        finally:
            self._clear_process(process_path)

    def _run_cycle(
        self,
        mode: str,
        cycle: int,
        dataset_track: str | None,
    ) -> RuntimeSnapshot:
        if mode == "live":
            live_summary = self.live_service.run_cycle(dataset_track=dataset_track)
            return RuntimeSnapshot(
                mode=mode,
                cycle=cycle,
                status=live_summary.status,
                system_status=live_summary.system_status,
                connectivity_state=live_summary.connectivity_state,
                timestamp=live_summary.timestamp,
                regime_state=live_summary.regime_state,
                risk_state=live_summary.risk_state,
                equity_usd=live_summary.equity_usd,
                cash_usd=live_summary.cash_usd,
                fill_count=live_summary.fill_count,
                holdings=live_summary.holdings,
                open_order_count=live_summary.open_order_count,
                incidents=live_summary.incidents,
                freeze_reason=live_summary.freeze_reason,
                decision_executed=live_summary.decision_executed,
                fills=[fill.to_dict() for fill in live_summary.fills],
                portfolio_drawdown=live_summary.portfolio_drawdown,
                target_weights=live_summary.target_weights,
                decision_actions=live_summary.decision_actions,
                decision_reasons=live_summary.decision_reasons,
            )

        simulate_summary = self.backtest_service.simulate_latest_cycle(
            dataset_track=dataset_track
        )
        return RuntimeSnapshot(
            mode=mode,
            cycle=cycle,
            status=simulate_summary.status,
            system_status="simulated",
            connectivity_state="simulated",
            timestamp=simulate_summary.timestamp,
            regime_state=simulate_summary.regime_state,
            risk_state=simulate_summary.risk_state,
            equity_usd=simulate_summary.equity_usd,
            cash_usd=simulate_summary.cash_usd,
            fill_count=simulate_summary.fill_count,
            holdings=simulate_summary.holdings,
            incidents=simulate_summary.incidents,
            freeze_reason=simulate_summary.freeze_reason,
            decision_executed=(
                simulate_summary.fill_count > 0 or simulate_summary.timestamp is not None
            ),
            fills=[fill.to_dict() for fill in simulate_summary.fills],
            portfolio_drawdown=simulate_summary.portfolio_drawdown,
            target_weights=simulate_summary.target_weights,
            decision_actions=simulate_summary.decision_actions,
            decision_reasons=simulate_summary.decision_reasons,
        )

    def _register_process(self, path: Path, mode: str, *, started_at: str) -> None:
        existing = self._read_process_state(path)
        if existing is not None and existing.pid != os.getpid() and pid_is_running(existing.pid):
            raise ValueError(
                f"Another runtime process is already active: pid {existing.pid} ({existing.mode})"
            )
        if existing is not None and not pid_is_running(existing.pid):
            path.unlink(missing_ok=True)

        state = RuntimeProcessState(
            pid=os.getpid(),
            mode=mode,
            started_at=started_at,
            config_path=str(self.config.config_path),
        )
        write_json(path, state.to_dict())

    def _write_runtime_context(
        self,
        *,
        mode: str,
        status: str,
        cycle_limit: int | None,
        completed_cycles: int,
        started_at: str,
        finished_at: str | None = None,
        last_snapshot: dict[str, object] | None = None,
        last_alerts: list[dict[str, object]] | None = None,
        error: str | None = None,
    ) -> None:
        payload = RuntimeContextState(
            pid=os.getpid(),
            mode=mode,
            status=status,
            cycle_limit=cycle_limit,
            completed_cycles=completed_cycles,
            started_at=started_at,
            updated_at=self._now_iso(),
            config_path=str(self.config.config_path),
            config_snapshot=sanitized_config_payload(self.config),
            finished_at=finished_at,
            last_snapshot=last_snapshot,
            last_alerts=last_alerts or [],
            error=error,
        ).to_dict()
        state_path = runtime_context_file(self.config.resolved_paths().state_dir)
        report_path = latest_runtime_context_report_file(self.config.resolved_paths().artifacts_dir)
        write_json(state_path, payload)
        write_json(report_path, payload)

    @staticmethod
    def _clear_process(path: Path) -> None:
        existing = RuntimeService._read_process_state(path)
        if existing is not None and existing.pid == os.getpid():
            path.unlink(missing_ok=True)

    @staticmethod
    def _read_process_state(path: Path) -> RuntimeProcessState | None:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RuntimeProcessState(
            pid=int(payload["pid"]),
            mode=str(payload["mode"]),
            started_at=str(payload["started_at"]),
            config_path=str(payload["config_path"]),
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=UTC).isoformat()
