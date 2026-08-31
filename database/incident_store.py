# PostgreSQL-backed incident storage for SentinelAI.
#
# The live monitor and API read/write incidents through this module so the
# application logic is separated from the database implementation.

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import psycopg
except ImportError:  # pragma: no cover - fallback for environments without the DB driver.
    psycopg = None

from database.config import get_database_url


def get_connection():
    """Create a connection to PostgreSQL when the driver is available."""
    if psycopg is None:
        raise RuntimeError("psycopg is not installed; falling back to JSONL storage.")
    return psycopg.connect(get_database_url())


def init_incidents_table():
    """Create the incidents table if it does not exist yet."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    resolved_at TIMESTAMPTZ,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    service VARCHAR(255) NOT NULL,
                    method VARCHAR(20),
                    path VARCHAR(255),
                    status_code INTEGER,
                    latency_ms DOUBLE PRECISION,
                    incident_type VARCHAR(100) NOT NULL,
                    severity VARCHAR(30) NOT NULL,
                    mode VARCHAR(50),
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incidents_status
                ON incidents (status);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incidents_service
                ON incidents (service);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incidents_type
                ON incidents (incident_type);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incidents_started_at
                ON incidents (started_at DESC);
                """
            )


def _coerce_datetime(value):
    """Normalize ISO timestamps into a Python datetime object."""
    if value in (None, ""):
        return None

    if isinstance(value, str):
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)

    return value


def _incident_to_row(incident: Dict[str, Any]):
    """Convert a Python incident dict into a DB-ready row."""
    metadata = {
        key: value
        for key, value in incident.items()
        if key not in {
            "incident_id",
            "status",
            "started_at",
            "resolved_at",
            "last_seen_at",
            "occurrence_count",
            "service",
            "method",
            "path",
            "status_code",
            "latency_ms",
            "incident_type",
            "severity",
            "mode",
            "timestamp",
        }
    }

    started_at = _coerce_datetime(incident.get("started_at") or incident.get("timestamp"))
    resolved_at = _coerce_datetime(incident.get("resolved_at"))
    last_seen_at = _coerce_datetime(
        incident.get("last_seen_at") or incident.get("started_at") or incident.get("timestamp")
    )

    return (
        incident["incident_id"],
        incident.get("status", "ACTIVE"),
        started_at,
        resolved_at,
        last_seen_at,
        incident.get("occurrence_count", 1),
        incident.get("service"),
        incident.get("method"),
        incident.get("path"),
        incident.get("status_code"),
        incident.get("latency_ms"),
        incident.get("incident_type"),
        incident.get("severity"),
        incident.get("mode"),
        json.dumps(metadata),
    )


def upsert_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Insert or update a single incident in PostgreSQL."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            row = _incident_to_row(incident)
            cur.execute(
                """
                INSERT INTO incidents (
                    incident_id,
                    status,
                    started_at,
                    resolved_at,
                    last_seen_at,
                    occurrence_count,
                    service,
                    method,
                    path,
                    status_code,
                    latency_ms,
                    incident_type,
                    severity,
                    mode,
                    metadata,
                    created_at,
                    updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW()
                )
                ON CONFLICT (incident_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    started_at = EXCLUDED.started_at,
                    resolved_at = EXCLUDED.resolved_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    occurrence_count = EXCLUDED.occurrence_count,
                    service = EXCLUDED.service,
                    method = EXCLUDED.method,
                    path = EXCLUDED.path,
                    status_code = EXCLUDED.status_code,
                    latency_ms = EXCLUDED.latency_ms,
                    incident_type = EXCLUDED.incident_type,
                    severity = EXCLUDED.severity,
                    mode = EXCLUDED.mode,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                row,
            )
            conn.commit()
    return incident


def get_incident_by_id(incident_id: str) -> Optional[Dict[str, Any]]:
    """Read one incident by UUID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    incident_id,
                    status,
                    started_at,
                    resolved_at,
                    last_seen_at,
                    occurrence_count,
                    service,
                    method,
                    path,
                    status_code,
                    latency_ms,
                    incident_type,
                    severity,
                    mode,
                    metadata
                FROM incidents
                WHERE incident_id = %s
                """,
                (incident_id,),
            )
            row = cur.fetchone()

    if row is None:
        return None

    return _row_to_incident(row)


def get_active_incidents() -> List[Dict[str, Any]]:
    """Return all active incidents."""
    return list_incidents_by_status("ACTIVE")


def resolve_incident_in_db(incident_id: str, resolved_at: Optional[str] = None) -> Dict[str, Any]:
    """Mark an incident as resolved in the database."""
    incident = get_incident_by_id(incident_id)
    if not incident:
        return {}

    incident["status"] = "RESOLVED"
    incident["resolved_at"] = resolved_at or incident.get("resolved_at") or incident.get("last_seen_at")
    incident["last_seen_at"] = incident.get("last_seen_at") or incident.get("started_at") or incident.get("resolved_at")
    upsert_incident(incident)
    return incident


def list_incidents_by_status(status: str) -> List[Dict[str, Any]]:
    """Return incidents with a specific lifecycle status."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    incident_id,
                    status,
                    started_at,
                    resolved_at,
                    last_seen_at,
                    occurrence_count,
                    service,
                    method,
                    path,
                    status_code,
                    latency_ms,
                    incident_type,
                    severity,
                    mode,
                    metadata
                FROM incidents
                WHERE status = %s
                ORDER BY started_at DESC, last_seen_at DESC
                """,
                (status,),
            )
            rows = cur.fetchall()

    return [_row_to_incident(row) for row in rows]


def list_incidents() -> List[Dict[str, Any]]:
    """Return all incidents newest first."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    incident_id,
                    status,
                    started_at,
                    resolved_at,
                    last_seen_at,
                    occurrence_count,
                    service,
                    method,
                    path,
                    status_code,
                    latency_ms,
                    incident_type,
                    severity,
                    mode,
                    metadata
                FROM incidents
                ORDER BY started_at DESC, last_seen_at DESC
                """
            )
            rows = cur.fetchall()

    return [_row_to_incident(row) for row in rows]


def _row_to_incident(row):
    """Convert a DB row back into the API-friendly incident dict."""
    (
        incident_id,
        status,
        started_at,
        resolved_at,
        last_seen_at,
        occurrence_count,
        service,
        method,
        path,
        status_code,
        latency_ms,
        incident_type,
        severity,
        mode,
        metadata,
    ) = row

    incident = {
        "incident_id": str(incident_id),
        "status": status,
        "started_at": started_at.isoformat() if started_at else None,
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
        "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
        "occurrence_count": occurrence_count,
        "service": service,
        "method": method,
        "path": path,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "incident_type": incident_type,
        "severity": severity,
        "mode": mode,
    }

    if metadata:
        for key, value in (metadata or {}).items():
            incident[key] = value

    if "timestamp" not in incident and started_at:
        incident["timestamp"] = started_at.isoformat()

    return incident


def ensure_incident_key(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure a DB-safe incident has a UUID and lifecycle fields."""
    if "incident_id" not in incident:
        incident["incident_id"] = str(__import__("uuid").uuid4())

    if "status" not in incident:
        incident["status"] = "ACTIVE"

    if "started_at" not in incident and "timestamp" in incident:
        incident["started_at"] = incident["timestamp"]

    if "last_seen_at" not in incident:
        incident["last_seen_at"] = incident.get("started_at") or incident.get("timestamp")

    if "occurrence_count" not in incident:
        incident["occurrence_count"] = 1

    return incident


def upsert_active_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Public helper used by the live monitor for active incidents."""
    incident = ensure_incident_key(incident)
    return upsert_incident(incident)


def resolve_incident_in_db(incident_id: str, resolved_at: str) -> bool:
    """Mark an incident as RESOLVED in PostgreSQL."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE incidents
                SET status = 'RESOLVED',
                    resolved_at = %s,
                    last_seen_at = COALESCE(last_seen_at, %s),
                    updated_at = NOW()
                WHERE incident_id = %s
                """,
                (resolved_at, resolved_at, incident_id),
            )
            updated = cur.rowcount > 0
            conn.commit()
    return updated


def delete_incident(incident_id: str) -> bool:
    """Delete a single incident from PostgreSQL."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM incidents WHERE incident_id = %s", (incident_id,))
            deleted = cur.rowcount > 0
            conn.commit()
    return deleted
