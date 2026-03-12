"""Unit tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradebot.config import (
    ConfigError,
    apply_strategy_preset,
    default_config_path,
    default_tradebot_home,
    ensure_app_home_initialized,
    identify_strategy_preset,
    initialize_app_home,
    load_config,
)


def write_config(root: Path, content: str) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_load_config_resolves_paths_and_env(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(
        tmp_path,
        """
app:
  environment: test
  log_level: DEBUG
  log_format: console
runtime:
  default_mode: simulate
  max_cycles: 2
  cycle_interval_seconds: 2
exchange:
  name: kraken
  base_currency: USD
  supplementary_exchanges: [binance, coinbase]
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts:
  email_recipient: trader@example.com
paths:
  data_dir: data
  artifacts_dir: artifacts
  features_dir: artifacts/features
  experiments_dir: artifacts/experiments
  logs_dir: runtime/logs
  state_dir: runtime/state
""",
    )
    env_path = tmp_path / ".env"
    env_path.write_text("KRAKEN_API_KEY=demo-key\nSMTP_PORT=2525\n", encoding="utf-8")
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)

    config = load_config(config_path=config_path, env_path=env_path)

    assert config.app.environment == "test"
    assert config.runtime.max_cycles == 2
    assert config.alerts.email_recipient == "trader@example.com"
    assert config.secrets.kraken_api_key == "demo-key"
    assert config.secrets.smtp_port == 2525
    assert config.research.default_dataset_track == "dynamic_universe_kraken_only"
    assert config.strategy.volatility_layer_enabled is False
    assert config.strategy.gradual_reduction_layer_enabled is False
    assert config.strategy.entry_momentum_floor == 0.0
    assert config.backtest.max_positions == 3
    assert config.backtest.neutral_exposure == 0.78
    assert config.resolved_paths().data_dir == (tmp_path / "data").resolve()
    assert config.resolved_paths().features_dir == (tmp_path / "artifacts" / "features").resolve()


def test_apply_strategy_preset_can_switch_to_max_profit_profile(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
    )

    config = load_config(config_path=config_path, env_path=tmp_path / ".env")
    aggressive = apply_strategy_preset(config, "max_profit")

    assert identify_strategy_preset(config) == "live_default"
    assert identify_strategy_preset(aggressive) == "max_profit"
    assert aggressive.backtest.max_positions == 3
    assert aggressive.backtest.neutral_exposure == 0.85
    assert aggressive.strategy.entry_momentum_floor == -0.02


def test_load_config_rejects_wrong_universe(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH]
alerts: {}
paths: {}
""",
    )

    with pytest.raises(ConfigError):
        load_config(config_path=config_path, env_path=tmp_path / ".env")


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(config_path=tmp_path / "config" / "settings.yaml", env_path=tmp_path / ".env")


def test_load_config_rejects_invalid_strategy_drawdown_order(tmp_path: Path) -> None:
  config_path = write_config(
    tmp_path,
    """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
  drawdown_caution_threshold: 0.2
  drawdown_reduced_threshold: 0.1
  drawdown_catastrophe_threshold: 0.3
alerts: {}
paths: {}
""",
  )

  with pytest.raises(ConfigError):
    load_config(config_path=config_path, env_path=tmp_path / ".env")


def test_load_config_rejects_invalid_dataset_track(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
research:
  default_dataset_track: unsupported_track
alerts: {}
paths: {}
""",
    )

    with pytest.raises(ConfigError):
        load_config(config_path=config_path, env_path=tmp_path / ".env")


def test_default_paths_prefer_tradebot_home_when_bot_config_path_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(tmp_path / "tradebot-home"))

    assert default_tradebot_home() == (tmp_path / "tradebot-home").resolve()
    assert default_config_path() == (
        tmp_path / "tradebot-home" / "config" / "settings.yaml"
    ).resolve()


def test_default_config_path_prefers_explicit_bot_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit_path = tmp_path / "repo" / "config" / "settings.yaml"
    monkeypatch.setenv("TRADEBOT_HOME", str(tmp_path / "tradebot-home"))
    monkeypatch.setenv("BOT_CONFIG_PATH", str(explicit_path))

    assert default_config_path() == explicit_path.resolve()


def test_initialize_app_home_creates_starter_layout(tmp_path: Path) -> None:
    summary = initialize_app_home(home=tmp_path / "tradebot-home")

    assert Path(str(summary["config_path"])).exists()
    assert Path(str(summary["env_path"])).exists()
    assert Path(str(summary["data_dir"])).exists()
    assert Path(str(summary["artifacts_dir"])).exists()
    assert Path(str(summary["runtime_dir"])).exists()


def test_ensure_app_home_initialized_creates_default_home_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "tradebot-home"
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("TRADEBOT_HOME", str(home))

    first_summary = ensure_app_home_initialized()
    second_summary = ensure_app_home_initialized()

    assert first_summary is not None
    assert Path(str(first_summary["config_path"])).exists()
    assert second_summary is None
