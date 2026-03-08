"""Unit tests for the runtime skeleton."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradebot.config import load_config
from tradebot.runtime import RuntimeService


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

    runtime = RuntimeService(config)
    snapshots = runtime.run(mode="simulate")

    assert len(snapshots) == 2
    expected_statuses = {"ok", "waiting_for_data", "waiting_for_signals"}
    assert all(snapshot.status in expected_statuses for snapshot in snapshots)
    assert (tmp_path / "data").exists()
    assert (tmp_path / "artifacts").exists()
    assert (tmp_path / "artifacts" / "models").exists()
    assert (tmp_path / "artifacts" / "reports" / "models").exists()
    assert (tmp_path / "runtime" / "logs").exists()
    assert (tmp_path / "runtime" / "state").exists()


def test_runtime_rejects_live_mode_until_phase_7(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime:
  default_mode: simulate
  max_cycles: 1
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")

    runtime = RuntimeService(config)

    with pytest.raises(NotImplementedError):
        runtime.run(mode="live")
