import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_API_DIR = ROOT / "services" / "sentinel-api"
ML_DIR = ROOT / "ml"

for folder in (str(ROOT), str(SENTINEL_API_DIR), str(ML_DIR)):
    if folder not in sys.path:
        sys.path.insert(0, folder)

from database.incident_store import init_incidents_table, list_incidents
from database.metric_store import get_recent_metrics, init_metrics_table, insert_metric
from ml.live_monitor import save_incident

sentinel_api_path = SENTINEL_API_DIR / "main.py"
sentinel_api_spec = importlib.util.spec_from_file_location("sentinel_api_main", sentinel_api_path)
sentinel_api_module = importlib.util.module_from_spec(sentinel_api_spec)
assert sentinel_api_spec.loader is not None
sentinel_api_spec.loader.exec_module(sentinel_api_module)
app = sentinel_api_module.app

client = TestClient(app)


def test_metric_persistence_and_monitor_incident_flow():
    init_metrics_table()
    init_incidents_table()

    metric = {
        "timestamp": "2026-08-31T12:00:00Z",
        "service": "payment-service",
        "method": "POST",
        "path": "/payments",
        "status": 500,
        "status_code": 500,
        "latency_ms": 1425.0,
        "mode": "fail",
    }

    insert_metric(metric)
    stored_metrics = get_recent_metrics(limit=1000)
    assert any(item["service"] == "payment-service" and item["status_code"] == 500 for item in stored_metrics)

    created = save_incident(metric, "HTTP_ERROR", "CRITICAL")
    assert created is True

    active_incidents = list_incidents()
    assert any(item["incident_type"] == "HTTP_ERROR" and item["service"] == "payment-service" for item in active_incidents)

    response = client.get("/api/anomalies")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    assert any(item["incident_type"] == "HTTP_ERROR" for item in payload["incidents"])

    metrics_response = client.get("/api/metrics")
    assert metrics_response.status_code == 200
    metrics_payload = metrics_response.json()
    assert metrics_payload["count"] >= 1
    assert any(item["service"] == "payment-service" for item in metrics_payload["metrics"])
