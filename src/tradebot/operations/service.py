"""Operational helpers for doctor, status, email, reports, and log inspection."""

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
from tradebot.config import AppConfig
from tradebot.execution.kraken import KrakenClient, KrakenClientError
from tradebot.execution.storage import latest_live_status_file, live_state_file
from tradebot.logging_config import get_logger, log_file
from tradebot.model.storage import active_model_pointer_file
from tradebot.operations.storage import latest_alerts_report_file, runtime_context_file
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

    def doctor_summary(self) -> dict[str, object]:
        """Return a config and connectivity summary for local preflight checks."""
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
                "models_dir": self.paths.models_dir,
                "model_reports_dir": self.paths.model_reports_dir,
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
                ok = False
                private_api = {
                    "configured": True,
                    "ok": False,
                    "error": str(exc),
                }
        else:
            if self.config.runtime.default_mode == "live":
                ok = False
            private_api = {
                "configured": False,
                "ok": False,
                "error": "Kraken private API key and secret are not configured",
                "required_for_mode": self.config.runtime.default_mode == "live",
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
        message["Subject"] = "[tradebot] Test email"
        message["From"] = self.config.secrets.smtp_username or "tradebot@localhost"
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
        """Return the latest known runtime, model, and report state."""
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
        active_model = self._read_json_file(active_model_pointer_file(self.paths.models_dir))
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
            "active_model": active_model,
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
                "model_id",
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

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _default_smtp_factory(host: str, port: int) -> smtplib.SMTP:
    return smtplib.SMTP(host, port, timeout=30)
