"""Unit tests for shared simulate and live runtime orchestration."""

from __future__ import annotations

from pathlib import Path

from tradebot.backtest.models import SimulationCycleSummary
from tradebot.config import load_config
from tradebot.execution.models import LiveCycleSummary
from tradebot.operations.storage import (
    latest_alerts_report_file,
    latest_runtime_context_report_file,
    runtime_context_file,
)
from tradebot.runtime import RuntimeService, runtime_process_file


def test_runtime_bootstrap_and_run(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime:
  default_mode: simulate
  max_cycles: 2
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths:
  data_dir: data
  artifacts_dir: artifacts
  logs_dir: runtime/logs
  state_dir: runtime/state
""",
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")

    class FakeBacktestService:
        def simulate_latest_cycle(
            self,
            dataset_track: str | None = None,
        ) -> SimulationCycleSummary:
            assert dataset_track is None
            return SimulationCycleSummary(
                dataset_id=None,
                timestamp=None,
                status="waiting_for_data",
                regime_state=None,
                risk_state=None,
                equity_usd=config.backtest.initial_cash_usd,
                cash_usd=config.backtest.initial_cash_usd,
                fill_count=0,
                fills=[],
                state_file=str(tmp_path / "runtime" / "state" / "simulate_state.json"),
            )

    sleep_calls: list[float] = []
    runtime = RuntimeService(
        config,
        backtest_service=FakeBacktestService(),
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )
    snapshots = runtime.run(mode="simulate")

    assert len(snapshots) == 2
    assert all(snapshot.status == "waiting_for_data" for snapshot in snapshots)
    assert sleep_calls == [config.runtime.cycle_interval_seconds]
    assert (tmp_path / "data").exists()
    assert (tmp_path / "artifacts").exists()
    assert (tmp_path / "artifacts" / "models").exists()
    assert (tmp_path / "artifacts" / "reports" / "models").exists()
    assert (tmp_path / "artifacts" / "reports" / "runtime").exists()
    assert (tmp_path / "runtime" / "logs").exists()
    assert (tmp_path / "runtime" / "state").exists()
    assert not runtime_process_file(tmp_path / "runtime" / "state").exists()
    assert runtime_context_file(tmp_path / "runtime" / "state").exists()
    assert latest_runtime_context_report_file(tmp_path / "artifacts").exists()
    assert latest_alerts_report_file(tmp_path / "artifacts").exists()


def test_runtime_runs_live_cycles_with_live_service(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
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
    env_path = tmp_path / ".env"
    env_path.write_text(
        """
KRAKEN_API_KEY=test-key
KRAKEN_API_SECRET=dGVzdA==
""".strip(),
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=env_path)

    class FakeLiveService:
        def run_cycle(self, dataset_track: str | None = None) -> LiveCycleSummary:
            assert dataset_track is None
            return LiveCycleSummary(
                dataset_id="dataset-1",
                timestamp=1_705_000_000,
                status="ok",
                system_status="online",
                connectivity_state="online",
                regime_state="constructive",
                risk_state="normal",
                equity_usd=1_050.0,
                cash_usd=900.0,
                fill_count=1,
                fills=[],
                holdings={"BTC": 0.5},
                open_order_count=0,
                incidents=["trade_executed"],
                state_file=str(tmp_path / "runtime" / "state" / "live_state.json"),
                freeze_reason=None,
                model_id="model-1",
                decision_executed=True,
            )

    runtime = RuntimeService(config, live_service=FakeLiveService(), sleep_fn=lambda _: None)
    snapshots = runtime.run(mode="live")

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.mode == "live"
    assert snapshot.status == "ok"
    assert snapshot.system_status == "online"
    assert snapshot.connectivity_state == "online"
    assert snapshot.holdings == {"BTC": 0.5}
    assert snapshot.model_id == "model-1"
    assert not runtime_process_file(tmp_path / "runtime" / "state").exists()
    context_payload = runtime_context_file(tmp_path / "runtime" / "state").read_text(
        encoding="utf-8"
    )
    assert '"status": "finished"' in context_payload
    assert '"mode": "live"' in context_payload
