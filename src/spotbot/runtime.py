"""Runtime orchestration skeleton for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass

from spotbot.config import AppConfig
from spotbot.constants import SUPPORTED_MODES
from spotbot.logging_config import get_logger


@dataclass(frozen=True)
class RuntimeSnapshot:
    """A single runtime cycle result."""

    mode: str
    cycle: int
    status: str


class RuntimeService:
    """Minimal runtime service shared by simulate and live mode bootstrapping."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = get_logger("spotbot.runtime")

    def bootstrap(self) -> None:
        """Ensure the runtime filesystem layout exists."""
        paths = self.config.resolved_paths()
        for directory in (
            paths.data_dir,
            paths.artifacts_dir,
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
            self.logger.info("runtime cycle completed", extra={"mode": mode, "cycle": cycle})
            snapshots.append(RuntimeSnapshot(mode=mode, cycle=cycle, status="ok"))

        self.logger.info("runtime finished", extra={"mode": mode, "completed_cycles": cycle_limit})
        return snapshots