"""Configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from spotbot.constants import BASE_CURRENCY, FIXED_UNIVERSE, PRIMARY_EXCHANGE, SUPPORTED_MODES


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or validated."""


class AppSettings(BaseModel):
    """Application-level settings."""

    environment: str = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


class RuntimeSettings(BaseModel):
    """Runtime settings shared by simulate and live mode."""

    default_mode: Literal["simulate", "live"] = "simulate"
    max_cycles: int = Field(default=1, ge=1)
    cycle_interval_seconds: float = Field(default=1.0, gt=0)


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

    @field_validator("fixed_universe")
    @classmethod
    def validate_fixed_universe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(value) != FIXED_UNIVERSE:
            raise ValueError("fixed_universe must match the documented V1 asset universe")
        return value


class AlertSettings(BaseModel):
    """Alert routing settings."""

    email_recipient: str | None = None


class PathsSettings(BaseModel):
    """Filesystem layout settings."""

    data_dir: Path = Path("data")
    artifacts_dir: Path = Path("artifacts")
    logs_dir: Path = Path("runtime/logs")
    state_dir: Path = Path("runtime/state")


class SecretSettings(BaseModel):
    """Secret values loaded from the environment."""

    kraken_api_key: str | None = None
    kraken_api_secret: str | None = None
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


def default_config_path() -> Path:
    """Resolve the default configuration path from the environment or repository root."""
    configured_path = os.getenv("BOT_CONFIG_PATH", "config/settings.yaml")
    return Path(configured_path).expanduser().resolve()


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
        resolved_env_path = (project_root / ".env").resolve()

    if resolved_env_path.exists():
        load_dotenv(resolved_env_path, override=False)

    if not resolved_config_path.exists():
        raise ConfigError(f"Configuration file does not exist: {resolved_config_path}")

    try:
        with resolved_config_path.open("r", encoding="utf-8") as handle:
            raw_config = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML config: {resolved_config_path}") from exc

    if not isinstance(raw_config, dict):
        raise ConfigError("Top-level YAML config must be a mapping")

    merged_config: dict[str, Any] = {
        **raw_config,
        "secrets": {
            "kraken_api_key": os.getenv("KRAKEN_API_KEY"),
            "kraken_api_secret": os.getenv("KRAKEN_API_SECRET"),
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