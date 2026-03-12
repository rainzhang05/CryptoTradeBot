"""Runtime alert classification, deduplication, and delivery."""

from __future__ import annotations

import json
import smtplib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any, Literal, cast

from tradebot.config import AppConfig
from tradebot.data.storage import write_json
from tradebot.logging_config import get_logger
from tradebot.operations.storage import alert_state_file, latest_alerts_report_file

if TYPE_CHECKING:
    from tradebot.runtime import RuntimeSnapshot

AlertSeverity = Literal["info", "warning", "critical"]

_EXCHANGE_FAILURE_MARKERS = (
    "exchange_connectivity",
    "exchange_status",
    "dead_man_switch",
    "account_sync",
    "order_management",
    "order_failures_exceeded",
    "account_reconciliation_failed",
)
_DATA_FAILURE_MARKERS = (
    "data_refresh",
    "stale_daily_signals",
    "latest_signal_not_kraken_native",
    "missing_live_price",
    "missing_signal",
    "low_source_confidence",
    "liquidity_invalid",
)


@dataclass(frozen=True)
class AlertEvent:
    """One emitted runtime alert."""

    dedupe_key: str
    event_class: str
    severity: AlertSeverity
    title: str
    message: str
    mode: str
    occurred_at: str
    snapshot_timestamp: int | None = None
    details: dict[str, object] = field(default_factory=dict)
    email_sent: bool = False
    email_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlertState:
    """Persisted runtime alert state used for deduplication and status views."""

    sent_keys: dict[str, str] = field(default_factory=dict)
    last_risk_state_by_mode: dict[str, str] = field(default_factory=dict)
    recent_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sent_keys": dict(self.sent_keys),
            "last_risk_state_by_mode": dict(self.last_risk_state_by_mode),
            "recent_events": list(self.recent_events),
        }


class RuntimeAlertService:
    """Classify runtime events into alert classes and deliver them safely."""

    def __init__(
        self,
        config: AppConfig,
        *,
        smtp_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.logger = get_logger("tradebot.alerts")
        self.smtp_factory = smtp_factory or _default_smtp_factory

    def process_snapshot(self, snapshot: RuntimeSnapshot) -> list[AlertEvent]:
        """Emit any alert-worthy events found in one runtime snapshot."""
        state = self._load_state()
        events = self._events_for_snapshot(snapshot, state)
        return self._record_events(
            events=events,
            state=state,
            mode=snapshot.mode,
            risk_state=snapshot.risk_state,
        )

    def process_startup_failure(self, *, mode: str, error: str) -> list[AlertEvent]:
        """Emit a startup-failure alert for runtime bootstrap problems."""
        state = self._load_state()
        event = self._event(
            dedupe_key=f"startup_failure:{mode}:{error}",
            event_class="startup_failure",
            severity="critical",
            title="Runtime startup failed",
            message=error,
            mode=mode,
            snapshot_timestamp=None,
            details={"error": error},
        )
        return self._record_events(events=[event], state=state, mode=mode, risk_state=None)

    def _events_for_snapshot(
        self,
        snapshot: RuntimeSnapshot,
        state: AlertState,
    ) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        snapshot_key = "n/a" if snapshot.timestamp is None else str(snapshot.timestamp)

        if snapshot.fill_count > 0:
            events.append(
                self._event(
                    dedupe_key=f"trade_executed:{snapshot.mode}:{snapshot_key}:{snapshot.fill_count}",
                    event_class="trade_executed",
                    severity="info",
                    title="Trade executed",
                    message=f"Executed {snapshot.fill_count} fill(s) in {snapshot.mode} mode.",
                    mode=snapshot.mode,
                    snapshot_timestamp=snapshot.timestamp,
                    details={
                        "fills": list(snapshot.fills),
                        "holdings": dict(snapshot.holdings),
                    },
                )
            )

        risk_actions = {
            asset: action
            for asset, action in snapshot.decision_actions.items()
            if action in {"reduce", "exit"}
        }
        if risk_actions:
            summary = ",".join(
                f"{asset}:{action}" for asset, action in sorted(risk_actions.items())
            )
            events.append(
                self._event(
                    dedupe_key=f"risk_reduction:{snapshot.mode}:{snapshot_key}:{summary}",
                    event_class="risk_reduction_triggered",
                    severity="warning",
                    title="Risk reduction triggered",
                    message=f"Strategy requested reductions or exits for {summary}.",
                    mode=snapshot.mode,
                    snapshot_timestamp=snapshot.timestamp,
                    details={
                        "asset_actions": risk_actions,
                        "asset_reasons": {
                            asset: snapshot.decision_reasons.get(asset, "")
                            for asset in risk_actions
                        },
                    },
                )
            )

        if snapshot.status == "frozen" and snapshot.freeze_reason:
            events.append(
                self._event(
                    dedupe_key=f"freeze:{snapshot.mode}:{snapshot_key}:{snapshot.freeze_reason}",
                    event_class="freeze_triggered",
                    severity="critical",
                    title="Freeze triggered",
                    message=snapshot.freeze_reason,
                    mode=snapshot.mode,
                    snapshot_timestamp=snapshot.timestamp,
                    details={
                        "freeze_reason": snapshot.freeze_reason,
                        "incidents": list(snapshot.incidents),
                    },
                )
            )

        for event_class, title in self._failure_classifications(snapshot):
            failure_key = (
                f"{event_class}:{snapshot.mode}:{snapshot_key}:"
                f"{snapshot.freeze_reason or '|'.join(snapshot.incidents)}"
            )
            events.append(
                self._event(
                    dedupe_key=failure_key,
                    event_class=event_class,
                    severity="critical",
                    title=title,
                    message=snapshot.freeze_reason or ", ".join(snapshot.incidents) or title,
                    mode=snapshot.mode,
                    snapshot_timestamp=snapshot.timestamp,
                    details={
                        "freeze_reason": snapshot.freeze_reason,
                        "incidents": list(snapshot.incidents),
                    },
                )
            )

        previous_risk_state = state.last_risk_state_by_mode.get(snapshot.mode)
        if snapshot.risk_state == "catastrophe" and previous_risk_state != "catastrophe":
            message = "Portfolio entered catastrophe risk state."
            if snapshot.portfolio_drawdown is not None:
                message = (
                    f"Portfolio entered catastrophe risk state at "
                    f"{snapshot.portfolio_drawdown:.2%} drawdown."
                )
            events.append(
                self._event(
                    dedupe_key=f"catastrophe:{snapshot.mode}:{snapshot_key}",
                    event_class="catastrophe_state_entered",
                    severity="critical",
                    title="Catastrophe state entered",
                    message=message,
                    mode=snapshot.mode,
                    snapshot_timestamp=snapshot.timestamp,
                    details={
                        "risk_state": snapshot.risk_state,
                        "portfolio_drawdown": snapshot.portfolio_drawdown,
                    },
                )
            )
        elif (
            snapshot.risk_state in {"elevated_caution", "reduced_aggressiveness"}
            and snapshot.risk_state != previous_risk_state
        ):
            message = f"Portfolio risk state advanced to {snapshot.risk_state}."
            if snapshot.portfolio_drawdown is not None:
                message = (
                    f"Portfolio risk state advanced to {snapshot.risk_state} at "
                    f"{snapshot.portfolio_drawdown:.2%} drawdown."
                )
            events.append(
                self._event(
                    dedupe_key=f"drawdown_threshold:{snapshot.mode}:{snapshot_key}:{snapshot.risk_state}",
                    event_class="portfolio_drawdown_threshold",
                    severity="warning",
                    title="Portfolio drawdown threshold reached",
                    message=message,
                    mode=snapshot.mode,
                    snapshot_timestamp=snapshot.timestamp,
                    details={
                        "risk_state": snapshot.risk_state,
                        "portfolio_drawdown": snapshot.portfolio_drawdown,
                    },
                )
            )

        return events

    def _record_events(
        self,
        *,
        events: list[AlertEvent],
        state: AlertState,
        mode: str,
        risk_state: str | None,
    ) -> list[AlertEvent]:
        sent_keys = dict(state.sent_keys)
        last_risk_state_by_mode = dict(state.last_risk_state_by_mode)
        if risk_state is not None:
            last_risk_state_by_mode[mode] = risk_state

        recent_events = list(state.recent_events)
        delivered: list[AlertEvent] = []
        for event in events:
            if event.dedupe_key in sent_keys:
                continue
            delivered_event = self._deliver(event)
            sent_keys[delivered_event.dedupe_key] = delivered_event.occurred_at
            recent_events.insert(0, delivered_event.to_dict())
            recent_events = recent_events[:50]
            delivered.append(delivered_event)

        updated_state = AlertState(
            sent_keys=self._trim_sent_keys(sent_keys),
            last_risk_state_by_mode=last_risk_state_by_mode,
            recent_events=recent_events,
        )
        self._persist_state(updated_state)
        return delivered

    def _deliver(self, event: AlertEvent) -> AlertEvent:
        delivered_event = self._deliver_email(event)
        log_fn = {
            "info": self.logger.info,
            "warning": self.logger.warning,
            "critical": self.logger.error,
        }[delivered_event.severity]
        log_fn(
            "alert emitted",
            extra={
                "event_class": delivered_event.event_class,
                "severity": delivered_event.severity,
                "mode": delivered_event.mode,
                "snapshot_timestamp": delivered_event.snapshot_timestamp,
                "email_sent": delivered_event.email_sent,
                "email_error": delivered_event.email_error,
            },
        )
        return delivered_event

    def _deliver_email(self, event: AlertEvent) -> AlertEvent:
        recipient = self.config.alerts.email_recipient
        if not recipient:
            return replace(event, email_error="email_recipient_not_configured")
        if not self.config.secrets.smtp_host:
            return replace(event, email_error="smtp_host_not_configured")

        message = EmailMessage()
        message["Subject"] = f"[cryptotradebot][{event.severity.upper()}] {event.title}"
        message["From"] = self.config.secrets.smtp_username or "cryptotradebot@localhost"
        message["To"] = recipient
        message.set_content(self._email_body(event))

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
        except (OSError, smtplib.SMTPException) as exc:
            self.logger.warning(
                "alert email delivery failed",
                extra={
                    "event_class": event.event_class,
                    "mode": event.mode,
                    "error": str(exc),
                },
            )
            return replace(event, email_error=str(exc))
        finally:
            try:
                smtp.quit()
            except (OSError, smtplib.SMTPException):
                pass

        return replace(event, email_sent=True, email_error=None)

    def _email_body(self, event: AlertEvent) -> str:
        return "\n".join(
            [
                event.message,
                "",
                f"mode: {event.mode}",
                f"snapshot_timestamp: {event.snapshot_timestamp}",
                f"occurred_at: {event.occurred_at}",
                "details:",
                json.dumps(event.details, indent=2, sort_keys=True),
            ]
        )

    def _failure_classifications(self, snapshot: RuntimeSnapshot) -> list[tuple[str, str]]:
        payload = " ".join(
            item for item in [snapshot.freeze_reason, *snapshot.incidents] if item
        )
        classifications: list[tuple[str, str]] = []
        if self._matches_any(payload, _EXCHANGE_FAILURE_MARKERS):
            classifications.append(("exchange_api_failure", "Exchange or API failure"))
        if self._matches_any(payload, _DATA_FAILURE_MARKERS):
            classifications.append(("data_integrity_failure", "Data gap or integrity failure"))
        return classifications

    def _load_state(self) -> AlertState:
        path = alert_state_file(self.paths.state_dir)
        if not path.exists():
            return AlertState()
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        return AlertState(
            sent_keys={
                str(key): str(value)
                for key, value in cast(dict[str, object], payload.get("sent_keys", {})).items()
            },
            last_risk_state_by_mode={
                str(key): str(value)
                for key, value in cast(
                    dict[str, object],
                    payload.get("last_risk_state_by_mode", {}),
                ).items()
            },
            recent_events=[
                cast(dict[str, Any], event)
                for event in cast(list[object], payload.get("recent_events", []))
                if isinstance(event, dict)
            ],
        )

    def _persist_state(self, state: AlertState) -> None:
        state_path = alert_state_file(self.paths.state_dir)
        report_path = latest_alerts_report_file(self.paths.artifacts_dir)
        write_json(state_path, state.to_dict())
        write_json(
            report_path,
            {
                "updated_at": self._now_iso(),
                "recent_events": state.recent_events,
            },
        )

    def _event(
        self,
        *,
        dedupe_key: str,
        event_class: str,
        severity: AlertSeverity,
        title: str,
        message: str,
        mode: str,
        snapshot_timestamp: int | None,
        details: dict[str, object],
    ) -> AlertEvent:
        return AlertEvent(
            dedupe_key=dedupe_key,
            event_class=event_class,
            severity=severity,
            title=title,
            message=message,
            mode=mode,
            occurred_at=self._now_iso(),
            snapshot_timestamp=snapshot_timestamp,
            details=details,
        )

    @staticmethod
    def _trim_sent_keys(sent_keys: dict[str, str], limit: int = 200) -> dict[str, str]:
        if len(sent_keys) <= limit:
            return sent_keys
        return dict(list(sent_keys.items())[-limit:])

    @staticmethod
    def _matches_any(payload: str, markers: tuple[str, ...]) -> bool:
        return any(marker in payload for marker in markers)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=UTC).isoformat()


def _default_smtp_factory(host: str, port: int) -> smtplib.SMTP:
    return smtplib.SMTP(host, port, timeout=30)
