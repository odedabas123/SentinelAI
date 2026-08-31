# PostgreSQL-backed request metrics storage for SentinelAI.
#
# The service middleware writes every request metric here first. We still keep
# the JSONL files as a temporary debugging fallback so production behavior is
# preserved even if the database is unavailable for a short time.

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import psycopg
except ImportError:  # pragma: no cover - fallback for environments without the DB driver.
    psycopg = None

from database.config import get_database_url


def get_connection():
    """Create a PostgreSQL connection for metric storage when the driver is installed."""
    if psycopg is None:
        raise RuntimeError("psycopg is not installed; falling back to JSONL storage.")
    return psycopg.connect(get_database_url())


def init_metrics_table():
    """Create the request_metrics table if it does not exist yet."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS request_metrics (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    service VARCHAR(255) NOT NULL,
                    method VARCHAR(20),
                    path VARCHAR(255),
                    status_code INTEGER,
                    latency_ms DOUBLE PRECISION,
                    mode VARCHAR(50),
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_request_metrics_timestamp
                ON request_metrics (timestamp DESC);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_request_metrics_service
                ON request_metrics (service);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_request_metrics_path
                ON request_metrics (path);
                """
            )


def _coerce_datetime(value):
    """Normalize common timestamp formats into a Python datetime object."""
    if value in (None, ""):
        return None

    if isinstance(value, str):
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)

    return value


def _metric_to_row(metric: Dict[str, Any]):
    """Convert a request metric dict into a database row."""
    metadata = {
        key: value
        for key, value in metric.items()
        if key not in {
            "timestamp",
            "service",
            "method",
            "path",
            "status",
            "status_code",
            "latency_ms",
            "mode",
        }
    }

    timestamp = _coerce_datetime(metric.get("timestamp"))
    status_code = metric.get("status_code")
    if status_code is None:
        status_code = metric.get("status")

    return (
        timestamp,
        metric.get("service"),
        metric.get("method"),
        metric.get("path"),
        status_code,
        metric.get("latency_ms"),
        metric.get("mode"),
        json.dumps(metadata),
    )


def insert_metric(metric: Dict[str, Any]) -> Dict[str, Any]:
    """Insert one request metric and return the stored row."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            row = _metric_to_row(metric)
            cur.execute(
                """
                INSERT INTO request_metrics (
                    timestamp,
                    service,
                    method,
                    path,
                    status_code,
                    latency_ms,
                    mode,
                    metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id, timestamp, service, method, path, status_code, latency_ms, mode
                """,
                row,
            )
            stored = cur.fetchone()
            conn.commit()

    if stored is None:
        return metric

    return _row_to_metric(stored)


def get_recent_metrics(limit: int = 100) -> List[Dict[str, Any]]:
    """Return the newest request metrics, newest first."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, timestamp, service, method, path, status_code, latency_ms, mode
                FROM request_metrics
                ORDER BY timestamp DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    return [_row_to_metric(row) for row in rows]


def get_metrics_after_id(metric_id: int, limit: int = 500) -> List[Dict[str, Any]]:
    """Return any metric rows with an ID greater than the specified value."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, timestamp, service, method, path, status_code, latency_ms, mode
                FROM request_metrics
                WHERE id > %s
                ORDER BY timestamp ASC, id ASC
                LIMIT %s
                """,
                (metric_id, limit),
            )
            rows = cur.fetchall()

    return [_row_to_metric(row) for row in rows]


def _row_to_metric(row):
    """Convert a database row back into the app's metric shape."""
    (metric_id, timestamp, service, method, path, status_code, latency_ms, mode) = row

    metric = {
        "id": metric_id,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "service": service,
        "method": method,
        "path": path,
        "status": status_code,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "mode": mode,
    }

    if metric["timestamp"] is None:
        metric["timestamp"] = datetime.now().isoformat()

    return metric
