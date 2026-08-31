import os
import uuid

from database.incident_store import (
    init_incidents_table,
    list_incidents,
    list_incidents_by_status,
    upsert_incident,
)


def test_incident_store_persists_and_lists_active_incidents():
    os.environ["POSTGRES_HOST"] = "localhost"
    os.environ["POSTGRES_PORT"] = "5432"
    os.environ["POSTGRES_DB"] = "sentinelai"
    os.environ["POSTGRES_USER"] = "sentinelai"
    os.environ["POSTGRES_PASSWORD"] = "sentinelai"

    init_incidents_table()

    incident_id = str(uuid.uuid4())
    incident = {
        "incident_id": incident_id,
        "status": "ACTIVE",
        "started_at": "2024-01-01T12:00:00Z",
        "last_seen_at": "2024-01-01T12:00:00Z",
        "occurrence_count": 3,
        "service": "payment-service",
        "method": "POST",
        "path": "/payments",
        "status_code": 500,
        "latency_ms": 1400.5,
        "incident_type": "HTTP_ERROR",
        "severity": "CRITICAL",
        "mode": "fail",
    }

    upsert_incident(incident)
    active = list_incidents_by_status("ACTIVE")

    assert any(item["incident_id"] == incident_id for item in active)
    assert any(item["service"] == "payment-service" for item in active)

    all_incidents = list_incidents()
    assert any(item["incident_id"] == incident_id for item in all_incidents)
