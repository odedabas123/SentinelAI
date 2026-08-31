# Tests for the core SentinelAI API contract.
# These checks keep the project portfolio-ready by verifying
# the health and anomaly endpoints still respond with the expected shape.

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_API_DIR = ROOT / "services" / "sentinel-api"

if str(SENTINEL_API_DIR) not in sys.path:
    sys.path.insert(0, str(SENTINEL_API_DIR))

from main import app  # noqa: E402

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_anomalies_response_contract():
    response = client.get("/api/anomalies")

    assert response.status_code == 200

    payload = response.json()

    required_keys = {
        "count",
        "active_count",
        "resolved_count",
        "legacy_count",
        "incidents",
    }

    assert required_keys.issubset(payload.keys())
    assert isinstance(payload["incidents"], list)
    assert payload["count"] == len(payload["incidents"])
