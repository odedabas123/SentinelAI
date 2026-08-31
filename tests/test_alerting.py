import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DIR = ROOT / "ml"
for folder in (str(ROOT), str(ML_DIR)):
    if folder not in sys.path:
        sys.path.insert(0, folder)

import ml.live_monitor as live_monitor
from alerting import WebhookNotifier


def build_incident():
    return {
        "incident_id": "incident-1",
        "status": "ACTIVE",
        "started_at": "2026-08-31T12:00:00Z",
        "last_seen_at": "2026-08-31T12:00:00Z",
        "occurrence_count": 1,
        "service": "payment-service",
        "method": "POST",
        "path": "/payments",
        "status_code": 500,
        "latency_ms": 1425.0,
        "incident_type": "HTTP_ERROR",
        "severity": "CRITICAL",
        "mode": "fail",
    }


def test_new_incident_alert_contains_operational_fields(monkeypatch):
    sent = []

    def fake_send(request, timeout):
        sent.append((request, timeout))

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()

    monkeypatch.setattr("alerting.urlopen", fake_send)
    notifier = WebhookNotifier("http://webhook.test/alerts")

    assert notifier.send_new_incident(build_incident()) is True
    payload = sent[0][0].data.decode("utf-8")
    assert '"incident_id": "incident-1"' in payload
    assert '"endpoint": "/payments"' in payload
    assert '"severity": "CRITICAL"' in payload

    assert notifier.send_new_incident({**build_incident(), "severity": "HIGH"}) is True
    assert notifier.send_new_incident({**build_incident(), "severity": "MEDIUM"}) is False


def test_monitor_alerts_only_on_create_and_once_on_resolution(monkeypatch):
    incident = build_incident()
    stored = {}
    created_alerts = []
    recovery_alerts = []

    class FakeNotifier:
        def send_new_incident(self, value):
            created_alerts.append(value.copy())

        def send_recovery(self, value):
            recovery_alerts.append(value.copy())

    monkeypatch.setattr(live_monitor, "alert_notifier", FakeNotifier())
    monkeypatch.setattr(live_monitor, "init_incidents_table", lambda: None)
    monkeypatch.setattr(live_monitor, "upsert_incident", lambda value: stored.update({value["incident_id"]: value.copy()}))
    monkeypatch.setattr(live_monitor, "get_incident_by_id", lambda incident_id: stored.get(incident_id, {}).copy())
    live_monitor.ACTIVE_INCIDENTS.clear()

    metric = {**incident, "status": 500, "timestamp": incident["started_at"]}
    assert live_monitor.save_incident(metric, "HTTP_ERROR", "CRITICAL") is True
    assert live_monitor.save_incident({**metric, "timestamp": "2026-08-31T12:01:00Z"}, "HTTP_ERROR", "CRITICAL") is False
    assert len(created_alerts) == 1

    assert live_monitor.resolve_incident(
        {**metric, "timestamp": "2026-08-31T12:02:00Z"},
        "HTTP_ERROR",
    ) is True
    assert len(recovery_alerts) == 1
    assert live_monitor.resolve_incident(
        {**metric, "timestamp": "2026-08-31T12:03:00Z"},
        "HTTP_ERROR",
    ) is False
    assert len(recovery_alerts) == 1


def test_webhook_failure_does_not_raise(monkeypatch):
    def failing_send(request, timeout):
        raise OSError("destination unavailable")

    monkeypatch.setattr("alerting.urlopen", failing_send)
    notifier = WebhookNotifier("http://webhook.test/alerts", recovery_enabled=True)

    assert notifier.send_new_incident(build_incident()) is False
    assert notifier.send_recovery({**build_incident(), "status": "RESOLVED"}) is False