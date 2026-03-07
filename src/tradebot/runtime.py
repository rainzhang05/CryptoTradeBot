"""Runtime orchestration skeleton for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass

from tradebot.backtest.service import BacktestService
from tradebot.config import AppConfig
from tradebot.constants import SUPPORTED_MODES
from tradebot.logging_config import get_logger


@dataclass(frozen=True)
class RuntimeSnapshot:
    """A single runtime cycle result."""

    mode: str
    cycle: int
    status: str
    timestamp: int | None = None
    regime_state: str | None = None
    equity_usd: float | None = None
    cash_usd: float | None = None
    fill_count: int = 0


class RuntimeService:
    """Minimal runtime service shared by simulate and live mode bootstrapping."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = get_logger("tradebot.runtime")
        self.backtest_service = BacktestService(config)

    def bootstrap(self) -> None:
        """Ensure the runtime filesystem layout exists."""
        paths = self.config.resolved_paths()
        for directory in (
            paths.data_dir,
            paths.artifacts_dir,
            paths.features_dir,
            paths.experiments_dir,
            paths.logs_dir,
            paths.state_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def run(self, mode: str, max_cycles: int | None = None) -> list[RuntimeSnapshot]:
        """Execute a bounded runtime loop for the requested mode."""
        if mode not in SUPPORTED_MODES:
            supported = ", ".join(SUPPORTED_MODES)
            raise ValueError(f"Unsupported mode '{mode}'. Expected one of: {supported}")

        self.bootstrap()
        cycle_limit = max_cycles if max_cycles is not None else self.config.runtime.max_cycles
        self.logger.info("runtime started", extra={"mode": mode, "cycle_limit": cycle_limit})

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
            snapshots.append(snapshot)

        self.logger.info("runtime finished", extra={"mode": mode, "completed_cycles": cycle_limit})
        return snapshots

    def _run_cycle(self, mode: str, cycle: int) -> RuntimeSnapshot:
        if mode == "simulate":
            summary = self.backtest_service.simulate_latest_cycle()
            return RuntimeSnapshot(
                mode=mode,
                cycle=cycle,
                status=summary.status,
                timestamp=summary.timestamp,
                regime_state=summary.regime_state,
                equity_usd=summary.equity_usd,
                cash_usd=summary.cash_usd,
                fill_count=summary.fill_count,
            )

        return RuntimeSnapshot(mode=mode, cycle=cycle, status="pending_live_engine")