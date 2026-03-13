"""Configuration loading, validation, and application-home bootstrap."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tradebot.constants import BASE_CURRENCY, FIXED_UNIVERSE, PRIMARY_EXCHANGE, SUPPORTED_MODES


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or validated."""


CRYPTOTRADEBOT_HOME_ENV = "CRYPTOTRADEBOT_HOME"
LEGACY_TRADEBOT_HOME_ENV = "TRADEBOT_HOME"
CRYPTOTRADEBOT_CONFIG_PATH_ENV = "CRYPTOTRADEBOT_CONFIG_PATH"
LEGACY_BOT_CONFIG_PATH_ENV = "BOT_CONFIG_PATH"


@dataclass(frozen=True)
class AppHomeLayout:
    """Resolved application-home layout used by global installations."""

    home: Path
    config_dir: Path
    config_path: Path
    env_path: Path
    data_dir: Path
    artifacts_dir: Path
    runtime_dir: Path

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


class AppSettings(BaseModel):
    """Application-level settings."""

    environment: str = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


class RuntimeSettings(BaseModel):
    """Runtime settings shared by simulate and live mode."""

    default_mode: Literal["simulate", "live"] = "simulate"
    max_cycles: int | None = None
    cycle_interval_seconds: float = Field(default=1.0, gt=0)
    live_order_poll_seconds: float = Field(default=2.0, gt=0)
    live_order_timeout_seconds: float = Field(default=20.0, gt=0)
    live_dead_man_switch_seconds: int = Field(default=60, ge=0)
    live_max_order_failures: int = Field(default=2, ge=1)

    @field_validator("max_cycles")
    @classmethod
    def validate_max_cycles(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("runtime.max_cycles must be at least 1 when provided")
        return value


class ExchangeSettings(BaseModel):
    """Exchange-related settings."""

    name: Literal["kraken"] = "kraken"
    base_currency: Literal["USD"] = "USD"
    supplementary_exchanges: tuple[str, ...] = ("binance", "coinbase")


class DataSettings(BaseModel):
    """Data ingestion and storage settings."""

    raw_kraken_dir: Path = Path("data/kraken_data")
    canonical_dir: Path = Path("data/canonical")
    reports_dir: Path = Path("artifacts/reports/data")
    intervals: tuple[Literal["1h", "1d"], ...] = ("1h", "1d")


class StrategySettings(BaseModel):
    """Strategy-level settings that are fixed in V1."""

    fixed_universe: tuple[str, ...] = FIXED_UNIVERSE
    regime_layer_enabled: bool = True
    entry_filter_layer_enabled: bool = True
    volatility_layer_enabled: bool = False
    gradual_reduction_layer_enabled: bool = False
    min_source_confidence: float = Field(default=0.8, ge=0, le=1)
    entry_momentum_floor: float = Field(default=0.0, ge=-1, le=1)
    entry_trend_gap_floor: float = Field(default=0.0, ge=-1, le=1)
    hold_momentum_floor: float = Field(default=-0.03, ge=-1, le=1)
    hold_trend_gap_floor: float = Field(default=-0.03, ge=-1, le=1)
    max_realized_volatility: float = Field(default=0.30, gt=0, le=5)
    reduction_volatility_threshold: float = Field(default=0.16, gt=0, le=5)
    severe_momentum_floor: float = Field(default=-0.08, ge=-1, le=1)
    severe_trend_gap_floor: float = Field(default=-0.05, ge=-1, le=1)
    weak_relative_strength_floor: float = Field(default=-0.08, ge=-1, le=1)
    reduction_target_fraction: float = Field(default=0.35, gt=0, lt=1)
    held_asset_score_bonus: float = Field(default=0.02, ge=0, le=1)
    drawdown_caution_threshold: float = Field(default=0.10, gt=0, lt=1)
    drawdown_reduced_threshold: float = Field(default=0.20, gt=0, lt=1)
    drawdown_catastrophe_threshold: float = Field(default=0.30, gt=0, lt=1)
    elevated_caution_exposure_multiplier: float = Field(default=0.96, gt=0, le=1)
    reduced_aggressiveness_exposure_multiplier: float = Field(default=0.78, gt=0, le=1)
    catastrophe_exposure_multiplier: float = Field(default=0.32, gt=0, le=1)

    @field_validator("fixed_universe")
    @classmethod
    def validate_fixed_universe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(value) != FIXED_UNIVERSE:
            raise ValueError("fixed_universe must match the documented V1 asset universe")
        return value

    @model_validator(mode="after")
    def validate_threshold_order(self) -> StrategySettings:
        if not (
            self.drawdown_caution_threshold
            < self.drawdown_reduced_threshold
            < self.drawdown_catastrophe_threshold
        ):
            raise ValueError(
                "strategy drawdown thresholds must satisfy caution < reduced < catastrophe"
            )
        if not (
            self.elevated_caution_exposure_multiplier
            >= self.reduced_aggressiveness_exposure_multiplier
            >= self.catastrophe_exposure_multiplier
        ):
            raise ValueError(
                "strategy exposure multipliers must satisfy elevated >= reduced >= catastrophe"
            )
        return self


class ResearchSettings(BaseModel):
    """Research and feature-generation settings for deterministic datasets."""

    primary_interval: Literal["1d"] = "1d"
    default_dataset_track: Literal["official_fixed_10", "dynamic_universe_kraken_only"] = (
        "dynamic_universe_kraken_only"
    )
    momentum_windows_days: tuple[int, ...] = (7, 30, 90)
    trend_windows_days: tuple[int, ...] = (50, 200)
    volatility_windows_days: tuple[int, ...] = (20, 60)
    relative_strength_window_days: int = Field(default=30, ge=2)
    breadth_window_days: int = Field(default=30, ge=2)
    dollar_volume_window_days: int = Field(default=20, ge=2)
    source_window_days: int = Field(default=30, ge=2)

    @field_validator(
        "momentum_windows_days",
        "trend_windows_days",
        "volatility_windows_days",
    )
    @classmethod
    def validate_windows(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("research window tuples must not be empty")
        if any(window < 2 for window in value):
            raise ValueError("research windows must be at least two days")
        return tuple(value)


class BacktestSettings(BaseModel):
    """Execution assumptions and portfolio constraints for backtest and simulate mode."""

    initial_cash_usd: float = Field(default=100_000.0, gt=0)
    fee_rate_bps: float = Field(default=26.0, ge=0)
    slippage_bps: float = Field(default=10.0, ge=0)
    max_positions: int = Field(default=3, ge=1, le=len(FIXED_UNIVERSE))
    max_asset_weight: float = Field(default=0.35, gt=0, le=0.35)
    min_order_notional_usd: float = Field(default=25.0, gt=0)
    rebalance_threshold: float = Field(default=0.05, ge=0, lt=1)
    quantity_precision: int = Field(default=8, ge=0, le=12)
    constructive_exposure: float = Field(default=1.0, ge=0, le=1)
    neutral_exposure: float = Field(default=0.78, ge=0, le=1)
    defensive_exposure: float = Field(default=0.45, ge=0, le=1)

    @model_validator(mode="after")
    def validate_exposure_order(self) -> BacktestSettings:
        if not (
            self.constructive_exposure >= self.neutral_exposure >= self.defensive_exposure
        ):
            raise ValueError(
                "backtest exposure scaling must satisfy constructive >= neutral >= defensive"
            )
        return self


class AlertSettings(BaseModel):
    """Alert routing settings."""

    email_recipient: str | None = None


class PathsSettings(BaseModel):
    """Filesystem layout settings."""

    data_dir: Path = Path("data")
    artifacts_dir: Path = Path("artifacts")
    features_dir: Path = Path("artifacts/features")
    experiments_dir: Path = Path("artifacts/experiments")
    logs_dir: Path = Path("runtime/logs")
    state_dir: Path = Path("runtime/state")


class SecretSettings(BaseModel):
    """Secret values loaded from the environment."""

    kraken_api_key: str | None = None
    kraken_api_secret: str | None = None
    kraken_api_otp: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None


class AppConfig(BaseModel):
    """Fully validated application configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    app: AppSettings
    runtime: RuntimeSettings
    exchange: ExchangeSettings
    data: DataSettings = Field(default_factory=DataSettings)
    strategy: StrategySettings
    research: ResearchSettings = Field(default_factory=ResearchSettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    alerts: AlertSettings
    paths: PathsSettings
    secrets: SecretSettings
    project_root: Path
    config_path: Path

    @model_validator(mode="after")
    def validate_documented_constraints(self) -> AppConfig:
        if self.runtime.default_mode not in SUPPORTED_MODES:
            raise ValueError("runtime.default_mode must match the documented supported modes")
        if self.exchange.name != PRIMARY_EXCHANGE:
            raise ValueError("exchange.name must remain kraken in V1")
        if self.exchange.base_currency != BASE_CURRENCY:
            raise ValueError("exchange.base_currency must remain USD in V1")
        return self

    def resolved_paths(self) -> PathsSettings:
        """Return absolute paths resolved against the project root."""
        return PathsSettings(
            data_dir=(self.project_root / self.paths.data_dir).resolve(),
            artifacts_dir=(self.project_root / self.paths.artifacts_dir).resolve(),
            features_dir=(self.project_root / self.paths.features_dir).resolve(),
            experiments_dir=(self.project_root / self.paths.experiments_dir).resolve(),
            logs_dir=(self.project_root / self.paths.logs_dir).resolve(),
            state_dir=(self.project_root / self.paths.state_dir).resolve(),
        )

    def resolved_data_settings(self) -> DataSettings:
        """Return absolute data-related paths resolved against the project root."""
        return DataSettings(
            raw_kraken_dir=(self.project_root / self.data.raw_kraken_dir).resolve(),
            canonical_dir=(self.project_root / self.data.canonical_dir).resolve(),
            reports_dir=(self.project_root / self.data.reports_dir).resolve(),
            intervals=self.data.intervals,
        )


STRATEGY_PRESETS: tuple[str, ...] = ("live_default", "max_profit")


def apply_strategy_preset(config: AppConfig, preset: str) -> AppConfig:
    """Return a config copy with a named strategy preset applied."""
    effective = config.model_copy(deep=True)
    if preset == "live_default":
        _apply_live_default_preset(effective)
        return effective
    if preset == "max_profit":
        _apply_max_profit_preset(effective)
        return effective
    raise ValueError(f"Unsupported strategy preset: {preset}")


def identify_strategy_preset(config: AppConfig) -> str:
    """Return the best-known preset label for the current config."""
    for preset in STRATEGY_PRESETS:
        candidate = apply_strategy_preset(config, preset)
        if _strategy_preset_fingerprint(candidate) == _strategy_preset_fingerprint(config):
            return preset
    return "custom"


def _apply_live_default_preset(config: AppConfig) -> None:
    config.strategy.regime_layer_enabled = True
    config.strategy.entry_filter_layer_enabled = True
    config.strategy.volatility_layer_enabled = False
    config.strategy.gradual_reduction_layer_enabled = False
    config.strategy.entry_momentum_floor = 0.0
    config.strategy.entry_trend_gap_floor = 0.0
    config.strategy.hold_momentum_floor = -0.03
    config.strategy.hold_trend_gap_floor = -0.03
    config.strategy.max_realized_volatility = 0.30
    config.strategy.reduction_volatility_threshold = 0.16
    config.strategy.reduction_target_fraction = 0.35
    config.strategy.held_asset_score_bonus = 0.02
    config.strategy.elevated_caution_exposure_multiplier = 0.96
    config.strategy.reduced_aggressiveness_exposure_multiplier = 0.78
    config.strategy.catastrophe_exposure_multiplier = 0.32
    config.backtest.max_positions = 3
    config.backtest.rebalance_threshold = 0.05
    config.backtest.neutral_exposure = 0.78
    config.backtest.defensive_exposure = 0.45


def _apply_max_profit_preset(config: AppConfig) -> None:
    config.strategy.regime_layer_enabled = True
    config.strategy.entry_filter_layer_enabled = True
    config.strategy.volatility_layer_enabled = False
    config.strategy.gradual_reduction_layer_enabled = False
    config.strategy.entry_momentum_floor = -0.02
    config.strategy.entry_trend_gap_floor = -0.01
    config.strategy.hold_momentum_floor = -0.08
    config.strategy.hold_trend_gap_floor = -0.06
    config.strategy.max_realized_volatility = 0.45
    config.strategy.reduction_volatility_threshold = 0.22
    config.strategy.reduction_target_fraction = 0.25
    config.strategy.held_asset_score_bonus = 0.03
    config.strategy.elevated_caution_exposure_multiplier = 1.0
    config.strategy.reduced_aggressiveness_exposure_multiplier = 0.85
    config.strategy.catastrophe_exposure_multiplier = 0.40
    config.backtest.max_positions = 3
    config.backtest.rebalance_threshold = 0.05
    config.backtest.neutral_exposure = 0.85
    config.backtest.defensive_exposure = 0.55


def _strategy_preset_fingerprint(config: AppConfig) -> tuple[object, ...]:
    return (
        config.strategy.regime_layer_enabled,
        config.strategy.entry_filter_layer_enabled,
        config.strategy.volatility_layer_enabled,
        config.strategy.gradual_reduction_layer_enabled,
        config.strategy.entry_momentum_floor,
        config.strategy.entry_trend_gap_floor,
        config.strategy.hold_momentum_floor,
        config.strategy.hold_trend_gap_floor,
        config.strategy.max_realized_volatility,
        config.strategy.reduction_volatility_threshold,
        config.strategy.reduction_target_fraction,
        config.strategy.held_asset_score_bonus,
        config.strategy.elevated_caution_exposure_multiplier,
        config.strategy.reduced_aggressiveness_exposure_multiplier,
        config.strategy.catastrophe_exposure_multiplier,
        config.backtest.max_positions,
        config.backtest.rebalance_threshold,
        config.backtest.neutral_exposure,
        config.backtest.defensive_exposure,
    )


def default_config_path() -> Path:
    """Resolve the default configuration path from explicit overrides or the app home."""
    configured_path = os.getenv(CRYPTOTRADEBOT_CONFIG_PATH_ENV) or os.getenv(
        LEGACY_BOT_CONFIG_PATH_ENV
    )
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return app_home_layout().config_path.resolve()


def default_tradebot_home() -> Path:
    """Resolve the default CryptoTradeBot home directory."""
    configured_home = os.getenv(CRYPTOTRADEBOT_HOME_ENV) or os.getenv(LEGACY_TRADEBOT_HOME_ENV)
    if configured_home:
        return Path(configured_home).expanduser().resolve()
    return (Path.home() / ".cryptotradebot").resolve()


def app_home_layout(home: Path | None = None) -> AppHomeLayout:
    """Return the default application-home layout for the given home root."""
    resolved_home = (home or default_tradebot_home()).expanduser().resolve()
    return AppHomeLayout(
        home=resolved_home,
        config_dir=resolved_home / "config",
        config_path=resolved_home / "config" / "settings.yaml",
        env_path=resolved_home / ".env",
        data_dir=resolved_home / "data",
        artifacts_dir=resolved_home / "artifacts",
        runtime_dir=resolved_home / "runtime",
    )


def default_config_payload() -> dict[str, Any]:
    """Return the starter configuration payload for a new application home."""
    return {
        "app": AppSettings().model_dump(mode="json"),
        "runtime": RuntimeSettings().model_dump(mode="json"),
        "exchange": ExchangeSettings().model_dump(mode="json"),
        "data": DataSettings().model_dump(mode="json"),
        "strategy": StrategySettings().model_dump(mode="json"),
        "research": ResearchSettings().model_dump(mode="json"),
        "backtest": BacktestSettings().model_dump(mode="json"),
        "alerts": AlertSettings().model_dump(mode="json"),
        "paths": PathsSettings().model_dump(mode="json"),
    }


def default_env_template() -> str:
    """Return the starter .env template for a new application home."""
    return "\n".join(
        [
            "# Kraken API credentials",
            "KRAKEN_API_KEY=",
            "KRAKEN_API_SECRET=",
            "KRAKEN_API_OTP=",
            "",
            "# SMTP alert delivery",
            "SMTP_HOST=",
            "SMTP_PORT=587",
            "SMTP_USERNAME=",
            "SMTP_PASSWORD=",
            "",
        ]
    )


def initialize_app_home(
    *,
    home: Path | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Create the application-home layout and starter files."""
    layout = app_home_layout(home)
    for directory in (
        layout.home,
        layout.config_dir,
        layout.data_dir,
        layout.data_dir / "kraken_data",
        layout.data_dir / "canonical",
        layout.artifacts_dir,
        layout.artifacts_dir / "reports",
        layout.runtime_dir,
        layout.runtime_dir / "logs",
        layout.runtime_dir / "state",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    config_created = force or not layout.config_path.exists()
    if config_created:
        layout.config_path.write_text(
            yaml.safe_dump(default_config_payload(), sort_keys=False),
            encoding="utf-8",
        )

    env_created = force or not layout.env_path.exists()
    if env_created:
        layout.env_path.write_text(default_env_template(), encoding="utf-8")

    return {
        "home": str(layout.home),
        "config_path": str(layout.config_path),
        "env_path": str(layout.env_path),
        "data_dir": str(layout.data_dir),
        "artifacts_dir": str(layout.artifacts_dir),
        "runtime_dir": str(layout.runtime_dir),
        "config_created": config_created,
        "env_created": env_created,
    }


def ensure_app_home_initialized() -> dict[str, object] | None:
    """Create the default application home on first use when no explicit config override exists."""
    if os.getenv(CRYPTOTRADEBOT_CONFIG_PATH_ENV) or os.getenv(LEGACY_BOT_CONFIG_PATH_ENV):
        return None
    layout = app_home_layout()
    if layout.config_path.exists():
        return None
    return initialize_app_home(home=layout.home, force=False)


def load_config(config_path: Path | None = None, env_path: Path | None = None) -> AppConfig:
    """Load configuration from YAML and environment variables."""
    resolved_config_path = (config_path or default_config_path()).expanduser().resolve()
    if resolved_config_path.parent.name == "config":
        project_root = resolved_config_path.parent.parent
    else:
        project_root = Path.cwd().resolve()

    if env_path:
        resolved_env_path = env_path.expanduser().resolve()
    else:
        default_layout = app_home_layout(project_root)
        resolved_env_path = default_layout.env_path.resolve()

    if resolved_env_path.exists():
        load_dotenv(resolved_env_path, override=env_path is not None)

    if not resolved_config_path.exists():
        raise ConfigError(f"Configuration file does not exist: {resolved_config_path}")

    try:
        with resolved_config_path.open("r", encoding="utf-8") as handle:
            raw_config = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML config: {resolved_config_path}") from exc

    if not isinstance(raw_config, dict):
        raise ConfigError("Top-level YAML config must be a mapping")
    raw_config = _upgrade_legacy_runtime_defaults(resolved_config_path, raw_config)

    merged_config: dict[str, Any] = {
        **raw_config,
        "secrets": {
            "kraken_api_key": os.getenv("KRAKEN_API_KEY"),
            "kraken_api_secret": os.getenv("KRAKEN_API_SECRET"),
            "kraken_api_otp": os.getenv("KRAKEN_API_OTP"),
            "smtp_host": os.getenv("SMTP_HOST"),
            "smtp_port": int(os.getenv("SMTP_PORT", "587")),
            "smtp_username": os.getenv("SMTP_USERNAME"),
            "smtp_password": os.getenv("SMTP_PASSWORD"),
        },
        "project_root": project_root,
        "config_path": resolved_config_path,
    }

    try:
        return AppConfig.model_validate(merged_config)
    except ValidationError as exc:
        raise ConfigError(f"Invalid application configuration: {exc}") from exc


def _upgrade_legacy_runtime_defaults(
    config_path: Path,
    raw_config: dict[str, Any],
) -> dict[str, Any]:
    runtime = raw_config.get("runtime")
    app = raw_config.get("app")
    if not isinstance(runtime, dict) or not isinstance(app, dict):
        return raw_config
    if runtime.get("max_cycles") != 1:
        return raw_config
    if app.get("environment", "local") != "local":
        return raw_config
    if app.get("log_level", "INFO") != "INFO":
        return raw_config
    if app.get("log_format", "json") != "json":
        return raw_config

    legacy_defaults: dict[str, object] = {
        "default_mode": "simulate",
        "cycle_interval_seconds": 1.0,
        "live_order_poll_seconds": 2.0,
        "live_order_timeout_seconds": 20.0,
        "live_dead_man_switch_seconds": 60,
        "live_max_order_failures": 2,
    }
    if any(runtime.get(key, value) != value for key, value in legacy_defaults.items()):
        return raw_config

    upgraded = {
        **raw_config,
        "runtime": {
            **runtime,
            "max_cycles": None,
        },
    }
    config_path.write_text(yaml.safe_dump(upgraded, sort_keys=False), encoding="utf-8")
    return upgraded


def sanitized_config_payload(config: AppConfig) -> dict[str, Any]:
    """Return a configuration payload without secret values."""
    payload = config.model_dump(mode="json", exclude={"secrets"})
    payload["secrets_present"] = {
        "kraken_api_key": bool(config.secrets.kraken_api_key),
        "kraken_api_secret": bool(config.secrets.kraken_api_secret),
        "kraken_api_otp": bool(config.secrets.kraken_api_otp),
        "smtp_host": bool(config.secrets.smtp_host),
        "smtp_username": bool(config.secrets.smtp_username),
        "smtp_password": bool(config.secrets.smtp_password),
    }
    return payload
