"""Operational helpers for setup, status, auth, email, reports, and log inspection."""

from __future__ import annotations

import json
import os
import shutil
import signal
import smtplib
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, cast

import yaml

from tradebot.backtest.storage import latest_backtest_report_file, simulate_state_file
from tradebot.config import AppConfig, app_home_layout
from tradebot.data.service import DataService
from tradebot.execution.kraken import KrakenClient, KrakenClientError
from tradebot.execution.storage import latest_live_status_file, live_state_file
from tradebot.logging_config import get_logger, log_file
from tradebot.operations.storage import latest_alerts_report_file, runtime_context_file
from tradebot.research.service import ResearchService
from tradebot.runtime import pid_is_running, runtime_process_file


class OperationsService:
    """Provide the operator-facing helper workflows required by the CLI."""

    def __init__(
        self,
        config: AppConfig,
        *,
        kraken_client: KrakenClient | None = None,
        smtp_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.data_settings = config.resolved_data_settings()
        self.logger = get_logger("tradebot.operations")
        self.kraken_client = kraken_client or KrakenClient(
            api_key=config.secrets.kraken_api_key,
            api_secret=config.secrets.kraken_api_secret,
            otp=config.secrets.kraken_api_otp,
        )
        self.smtp_factory = smtp_factory or _default_smtp_factory

    def preflight_summary(self, *, require_private: bool = False) -> dict[str, object]:
        """Return a config and connectivity summary for operator readiness checks."""
        path_entries = {
            name: {
                "path": str(path),
                "exists": path.exists(),
            }
            for name, path in {
                "data_dir": self.paths.data_dir,
                "artifacts_dir": self.paths.artifacts_dir,
                "features_dir": self.paths.features_dir,
                "experiments_dir": self.paths.experiments_dir,
                "logs_dir": self.paths.logs_dir,
                "state_dir": self.paths.state_dir,
            }.items()
        }

        public_api: dict[str, object]
        private_api: dict[str, object]
        ok = True
        try:
            system_status = self.kraken_client.get_system_status()
            public_api = {
                "ok": True,
                "status": system_status["status"],
                "timestamp": system_status["timestamp"],
                "message": system_status["message"],
            }
        except KrakenClientError as exc:
            ok = False
            public_api = {
                "ok": False,
                "error": str(exc),
            }

        if self.config.secrets.kraken_api_key and self.config.secrets.kraken_api_secret:
            try:
                balances = self.kraken_client.get_balances()
                private_api = {
                    "configured": True,
                    "ok": True,
                    "asset_count": len(balances),
                }
            except KrakenClientError as exc:
                private_api = {
                    "configured": True,
                    "ok": False,
                    "error": str(exc),
                }
                if require_private:
                    ok = False
        else:
            if require_private:
                ok = False
            private_api = {
                "configured": False,
                "ok": False,
                "error": "Kraken private API key and secret are not configured",
                "required_for_live": True,
            }
        return {
            "ok": ok,
            "config_path": str(self.config.config_path),
            "project_root": str(self.config.project_root),
            "exchange": {
                "name": self.config.exchange.name,
                "base_currency": self.config.exchange.base_currency,
                "public_api": public_api,
                "private_api": private_api,
            },
            "default_mode": self.config.runtime.default_mode,
            "email_configured": bool(self.config.alerts.email_recipient),
            "paths": path_entries,
        }

    def doctor_summary(self) -> dict[str, object]:
        """Return the older strict preflight summary used by legacy callers."""
        return self.preflight_summary(require_private=self.config.runtime.default_mode == "live")

    def setup_summary(self, assets: tuple[str, ...] | None = None) -> dict[str, object]:
        """Prepare runtime-ready data and summarize operator readiness."""
        selected_assets = assets or self.config.strategy.fixed_universe
        readiness = self.preflight_summary(require_private=False)
        exchange_summary = cast(dict[str, object], readiness["exchange"])
        public_api = cast(dict[str, object], exchange_summary["public_api"])
        private_api = cast(dict[str, object], exchange_summary["private_api"])
        if not bool(public_api["ok"]):
            raise ValueError("Kraken public system status check failed during setup")

        data_service = DataService(self.config)
        completion = data_service.complete_canonical(
            assets=selected_assets,
            allow_synthetic=False,
        )
        integrity = data_service.check_canonical(assets=selected_assets).to_dict()
        features = ResearchService(self.config).build_feature_store(
            assets=selected_assets,
            dataset_track=self.config.research.default_dataset_track,
        ).to_dict()

        data_ready = self._completion_is_ready(completion) and self._integrity_is_ready(integrity)
        credentials_complete = bool(
            self.config.secrets.kraken_api_key and self.config.secrets.kraken_api_secret
        )
        private_auth_ready = credentials_complete and bool(private_api.get("ok"))
        missing_for_live: list[str] = []
        if not self.config.secrets.kraken_api_key:
            missing_for_live.append("KRAKEN_API_KEY")
        if not self.config.secrets.kraken_api_secret:
            missing_for_live.append("KRAKEN_API_SECRET")
        if credentials_complete and not private_auth_ready:
            missing_for_live.append("kraken_private_auth_validation")

        setup_ok = bool(readiness["ok"]) and data_ready
        return {
            "ok": setup_ok,
            "ready_for_live": setup_ok and private_auth_ready,
            "ready_for_live_after_auth": setup_ok and not credentials_complete,
            "selected_assets": list(selected_assets),
            "missing_for_live": missing_for_live,
            "exchange": exchange_summary,
            "default_mode": readiness["default_mode"],
            "email_configured": readiness["email_configured"],
            "paths": cast(dict[str, object], readiness["paths"]),
            "data_completion": completion,
            "integrity": integrity,
            "features": features,
        }

    def set_kraken_auth(
        self,
        api_key: str,
        *,
        api_secret: str | None = None,
        otp: str | None = None,
    ) -> dict[str, object]:
        """Persist Kraken private-auth values into the active environment file."""
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("Kraken API key must not be empty")
        normalized_secret = None if api_secret is None else api_secret.strip()
        normalized_otp = None if otp is None else otp.strip()

        env_path = self._env_path()
        updates = {"KRAKEN_API_KEY": normalized_key}
        if normalized_secret is not None:
            updates["KRAKEN_API_SECRET"] = normalized_secret
        if normalized_otp is not None:
            updates["KRAKEN_API_OTP"] = normalized_otp
        self._update_env_file(env_path, updates)

        current_values = self._read_env_pairs(env_path)
        api_key_configured = bool(current_values.get("KRAKEN_API_KEY"))
        api_secret_configured = bool(current_values.get("KRAKEN_API_SECRET"))
        otp_configured = bool(current_values.get("KRAKEN_API_OTP"))
        missing_for_live: list[str] = []
        if not api_key_configured:
            missing_for_live.append("KRAKEN_API_KEY")
        if not api_secret_configured:
            missing_for_live.append("KRAKEN_API_SECRET")
        return {
            "env_path": str(env_path),
            "api_key_configured": api_key_configured,
            "api_secret_configured": api_secret_configured,
            "otp_configured": otp_configured,
            "live_ready": api_key_configured and api_secret_configured,
            "missing_for_live": missing_for_live,
        }

    def set_email_recipient(self, recipient: str) -> dict[str, object]:
        """Persist the configured alert email recipient into the YAML config file."""
        if "@" not in recipient:
            raise ValueError("Alert email recipient must be a valid email address")

        payload = self._config_payload()
        alerts = cast(dict[str, Any], payload.setdefault("alerts", {}))
        alerts["email_recipient"] = recipient
        self.config.config_path.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
        return {
            "config_path": str(self.config.config_path),
            "email_recipient": recipient,
        }

    def send_test_email(self, recipient: str | None = None) -> dict[str, object]:
        """Send a direct SMTP test email to the configured or provided recipient."""
        target = recipient or self.config.alerts.email_recipient
        if not target:
            raise ValueError("No alert email recipient is configured")
        if not self.config.secrets.smtp_host:
            raise ValueError("SMTP host is not configured in the environment")

        message = EmailMessage()
        message["Subject"] = "[cryptotradebot] Test email"
        message["From"] = self.config.secrets.smtp_username or "cryptotradebot@localhost"
        message["To"] = target
        message.set_content(
            "This is a test email from the crypto spot trading bot CLI."
        )

        smtp = self.smtp_factory(
            self.config.secrets.smtp_host,
            self.config.secrets.smtp_port,
        )
        try:
            if hasattr(smtp, "ehlo"):
                smtp.ehlo()
            if hasattr(smtp, "starttls"):
                smtp.starttls()
            if self.config.secrets.smtp_username and self.config.secrets.smtp_password:
                smtp.login(
                    self.config.secrets.smtp_username,
                    self.config.secrets.smtp_password,
                )
            smtp.send_message(message)
        finally:
            smtp.quit()

        return {
            "recipient": target,
            "smtp_host": self.config.secrets.smtp_host,
            "sent_at": datetime.now(tz=UTC).isoformat(),
        }

    def runtime_status(self) -> dict[str, object]:
        """Return the latest known runtime and report state."""
        process_path = runtime_process_file(self.paths.state_dir)
        managed_process = None
        if process_path.exists():
            managed_process = self._read_json_file(process_path)
            if managed_process is not None and "pid" in managed_process:
                managed_process["running"] = pid_is_running(int(managed_process["pid"]))

        live_status = self._read_json_file(latest_live_status_file(self.paths.artifacts_dir))
        live_state = self._read_json_file(live_state_file(self.paths.state_dir))
        simulate_state = self._read_json_file(simulate_state_file(self.paths.state_dir))
        latest_backtest = self._read_json_file(
            latest_backtest_report_file(self.paths.artifacts_dir)
        )
        runtime_context = self._read_json_file(runtime_context_file(self.paths.state_dir))
        latest_alerts = self._read_json_file(latest_alerts_report_file(self.paths.artifacts_dir))

        return {
            "managed_process": managed_process,
            "runtime_context": runtime_context,
            "latest_alerts": latest_alerts,
            "live_status": live_status,
            "live_state": live_state,
            "simulate_state": simulate_state,
            "latest_backtest": latest_backtest,
        }

    def stop_runtime(self) -> dict[str, object]:
        """Stop the tracked runtime process via SIGTERM when it is active."""
        process_path = runtime_process_file(self.paths.state_dir)
        process = self._read_json_file(process_path)
        if process is None:
            raise FileNotFoundError("No managed runtime process file exists")

        pid = int(process["pid"])
        if not pid_is_running(pid):
            process_path.unlink(missing_ok=True)
            raise ValueError(f"Recorded runtime process is not running: pid {pid}")

        os.kill(pid, signal.SIGTERM)
        return {
            "pid": pid,
            "mode": process.get("mode"),
            "status": "termination_requested",
        }

    def list_reports(self) -> list[dict[str, object]]:
        """List stored artifact files beneath the project's artifacts directory."""
        if not self.paths.artifacts_dir.exists():
            return []

        entries: list[dict[str, object]] = []
        for path in sorted(self.paths.artifacts_dir.rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            relative_path = path.relative_to(self.config.project_root)
            entries.append(
                {
                    "path": str(relative_path),
                    "category": "report" if "reports" in relative_path.parts else "artifact",
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=UTC,
                    ).isoformat(),
                }
            )
        return entries

    def export_report(self, source: str, destination: Path) -> dict[str, object]:
        """Copy a stored artifact or report file to a chosen destination."""
        source_path = self._resolve_source_path(source)
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"Report or artifact does not exist: {source}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        return {
            "source": str(source_path),
            "destination": str(destination),
            "size_bytes": destination.stat().st_size,
        }

    def tail_logs(self, lines: int = 50) -> list[str]:
        """Return the most recent structured log lines rendered for the terminal."""
        path = log_file(self.paths.logs_dir)
        if not path.exists():
            raise FileNotFoundError(f"Log file does not exist yet: {path}")

        rendered: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines()[-lines:]:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = cast(dict[str, Any], json.loads(stripped))
            except json.JSONDecodeError:
                rendered.append(stripped)
                continue

            segments = [
                str(payload.get("asctime", "")),
                str(payload.get("levelname", "")),
                f"[{payload.get('name', 'root')}]",
                str(payload.get("message", "")),
            ]
            for key in (
                "event_class",
                "severity",
                "mode",
                "status",
                "freeze_reason",
                "dataset_id",
                "fill_count",
                "email_sent",
                "email_error",
            ):
                if key in payload and payload[key] not in {None, ""}:
                    segments.append(f"{key}={payload[key]}")
            rendered.append(" ".join(segments).strip())
        return rendered

    def _resolve_source_path(self, source: str) -> Path:
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = (self.config.project_root / source_path).resolve()
        return source_path

    def _config_payload(self) -> dict[str, Any]:
        if not self.config.config_path.exists():
            raise FileNotFoundError(f"Config file does not exist: {self.config.config_path}")
        payload = yaml.safe_load(self.config.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("Config file payload must be a mapping")
        return cast(dict[str, Any], payload)

    def _env_path(self) -> Path:
        if self.config.config_path.parent.name == "config":
            return app_home_layout(self.config.project_root).env_path
        return (self.config.project_root / ".env").resolve()

    @staticmethod
    def _completion_is_ready(summary: dict[str, object]) -> bool:
        assets = cast(list[dict[str, object]], summary.get("assets", []))
        allowed_statuses = {"continuous", "up_to_date"}
        return all(
            str(interval.get("status")) in allowed_statuses
            for asset in assets
            for interval in cast(list[dict[str, object]], asset.get("intervals", []))
        )

    @staticmethod
    def _integrity_is_ready(summary: dict[str, object]) -> bool:
        results = cast(list[dict[str, object]], summary.get("results", []))
        return all(
            OperationsService._int_value(result.get("missing_intervals")) == 0
            and OperationsService._int_value(result.get("duplicate_timestamps")) == 0
            and OperationsService._int_value(result.get("out_of_order_timestamps")) == 0
            and OperationsService._int_value(result.get("non_positive_rows")) == 0
            for result in results
        )

    @staticmethod
    def _int_value(value: object | None) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(value)
        raise ValueError(f"Unsupported integer-like value: {value!r}")

    @staticmethod
    def _read_env_pairs(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        pairs: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            pairs[key] = value
        return pairs

    @staticmethod
    def _update_env_file(path: Path, updates: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        remaining = dict(updates)
        rendered: list[str] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in raw_line:
                rendered.append(raw_line)
                continue
            key, _value = raw_line.split("=", 1)
            if key in remaining:
                rendered.append(f"{key}={remaining.pop(key)}")
            else:
                rendered.append(raw_line)
        for key, value in remaining.items():
            rendered.append(f"{key}={value}")
        path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _default_smtp_factory(host: str, port: int) -> smtplib.SMTP:
    return smtplib.SMTP(host, port, timeout=30)
