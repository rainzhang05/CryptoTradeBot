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
from tradebot.config import AppConfig
from tradebot.constants import SUPPORTED_MODES
from tradebot.data.storage import write_json
from tradebot.execution.service import LiveExecutionService
from tradebot.logging_config import get_logger


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
    model_id: str | None = None
    decision_executed: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a machine-friendly runtime snapshot payload."""
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
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.logger = get_logger("tradebot.runtime")
        self.backtest_service = backtest_service or BacktestService(config)
        self.live_service = live_service or LiveExecutionService(config)
        self.sleep_fn = sleep_fn or default_sleep

    def bootstrap(self) -> None:
        """Ensure the runtime filesystem layout exists."""
        paths = self.config.resolved_paths()
        for directory in (
            paths.data_dir,
            paths.artifacts_dir,
            paths.features_dir,
            paths.experiments_dir,
            paths.models_dir,
            paths.model_reports_dir,
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
        on_cycle: Callable[[RuntimeSnapshot], None] | None = None,
    ) -> list[RuntimeSnapshot]:
        """Execute a bounded runtime loop for the requested mode."""
        if mode not in SUPPORTED_MODES:
            supported = ", ".join(SUPPORTED_MODES)
            raise ValueError(f"Unsupported mode '{mode}'. Expected one of: {supported}")

        self.bootstrap()
        cycle_limit = max_cycles if max_cycles is not None else self.config.runtime.max_cycles
        if mode == "live" and (
            not self.config.secrets.kraken_api_key or not self.config.secrets.kraken_api_secret
        ):
            raise ValueError("Live mode requires Kraken API key and secret in the environment")
        process_path = runtime_process_file(self.config.resolved_paths().state_dir)
        self._register_process(process_path, mode)
        self.logger.info("runtime started", extra={"mode": mode, "cycle_limit": cycle_limit})

        try:
            snapshots: list[RuntimeSnapshot] = []
            for cycle in range(1, cycle_limit + 1):
                snapshot = self._run_cycle(mode=mode, cycle=cycle)
                self.logger.info(
                    "runtime cycle completed",
                    extra={
                        "mode": mode,
                        "cycle": cycle,
                        "status": snapshot.status,
                        "fill_count": snapshot.fill_count,
                    },
                )
                if on_cycle is not None:
                    on_cycle(snapshot)
                snapshots.append(snapshot)
                if cycle < cycle_limit:
                    self.sleep_fn(self.config.runtime.cycle_interval_seconds)

            self.logger.info(
                "runtime finished",
                extra={"mode": mode, "completed_cycles": cycle_limit},
            )
            return snapshots
        finally:
            self._clear_process(process_path)

    def _run_cycle(self, mode: str, cycle: int) -> RuntimeSnapshot:
        if mode == "live":
            live_summary = self.live_service.run_cycle()
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
                model_id=live_summary.model_id,
                decision_executed=live_summary.decision_executed,
            )

        simulate_summary = self.backtest_service.simulate_latest_cycle()
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
            freeze_reason=simulate_summary.freeze_reason,
            model_id=simulate_summary.model_id,
            decision_executed=(
                simulate_summary.fill_count > 0 or simulate_summary.timestamp is not None
            ),
        )

    def _register_process(self, path: Path, mode: str) -> None:
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
            started_at=datetime.now(tz=UTC).isoformat(),
            config_path=str(self.config.config_path),
        )
        write_json(path, state.to_dict())

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
