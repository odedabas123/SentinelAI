"""Webhook notifications for SentinelAI incident lifecycle events."""

import json
import os
from typing import Any, Dict
from urllib.request import Request, urlopen


ALERT_SEVERITIES = {"HIGH", "CRITICAL"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class WebhookNotifier:
    """Send incident lifecycle payloads to one generic webhook destination."""

    def __init__(
        self,
        webhook_url: str = "",
        recovery_enabled: bool = False,
        timeout_seconds: float = 5.0,
    ):
        self.webhook_url = webhook_url.strip()
        self.recovery_enabled = recovery_enabled
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "WebhookNotifier":
        """Build notifier configuration from environment variables."""
        try:
            timeout = float(os.getenv("ALERT_WEBHOOK_TIMEOUT_SECONDS", "5"))
        except ValueError:
            timeout = 5.0
        return cls(
            webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
            recovery_enabled=_as_bool(os.getenv("ALERT_RECOVERY_ENABLED")),
            timeout_seconds=timeout,
        )

    def send_new_incident(self, incident: Dict[str, Any]) -> bool:
        """Send one alert for a newly created HIGH or CRITICAL incident."""
        if incident.get("severity", "").upper() not in ALERT_SEVERITIES:
            return False
        return self._send("incident.created", incident)

    def send_recovery(self, incident: Dict[str, Any]) -> bool:
        """Optionally send one recovery event when an incident is resolved."""
        if not self.recovery_enabled:
            return False
        return self._send("incident.resolved", incident)

    def _send(self, event: str, incident: Dict[str, Any]) -> bool:
        if not self.webhook_url:
            return False

        payload = {
            "event": event,
            "incident": {
                "incident_id": incident.get("incident_id"),
                "status": incident.get("status"),
                "service": incident.get("service"),
                "incident_type": incident.get("incident_type"),
                "severity": incident.get("severity"),
                "endpoint": incident.get("path"),
                "path": incident.get("path"),
                "started_at": incident.get("started_at") or incident.get("timestamp"),
                "last_seen_at": incident.get("last_seen_at"),
                "resolved_at": incident.get("resolved_at"),
                "occurrence_count": incident.get("occurrence_count"),
                "status_code": incident.get("status_code"),
                "latency_ms": incident.get("latency_ms"),
                "mode": incident.get("mode"),
            },
        }

        request = Request(
            self.webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds):
                return True
        except Exception as error:
            # A notification outage must never stop metric processing.
            print(f"Alert webhook failed: {error}")
            return False