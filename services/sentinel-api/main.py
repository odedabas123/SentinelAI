# Used to read JSON data
import json
import os
import sys

# Lets us work with safe file paths
from pathlib import Path

# httpx lets SentinelAI check monitored services over HTTP
import httpx

# FastAPI is our web framework
from fastapi import FastAPI

# Allows the frontend on port 3000
# to communicate with this API on port 8002
from fastapi.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.incident_store import init_incidents_table, list_incidents
from database.metric_store import get_recent_metrics, init_metrics_table as init_metric_table


# ==========================================
# CREATE SENTINELAI API
# ==========================================

app = FastAPI(
    title="SentinelAI API"
)


# ==========================================
# CORS CONFIGURATION
# ==========================================

app.add_middleware(
    CORSMiddleware,

    # A comma-separated origin list supports both local and hosted frontends.
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ==========================================
# FILE PATHS
# ==========================================

# Folder containing this main.py file
BASE_DIR = Path(__file__).resolve().parent


# Live Payment Service metrics
PAYMENT_METRICS_FILE = (
    BASE_DIR
    / ".."
    / "payment-service"
    / "metrics.jsonl"
).resolve()


# ==========================================
# SERVICE CONFIGURATION
# ==========================================

# When we run everything manually,
# these default to localhost.
#
# Docker Compose will replace them with:
#
# http://order-service:8000
# http://payment-service:8001
#
# Docker containers can find each other
# using their service names.

ORDER_SERVICE_URL = os.getenv(
    "ORDER_SERVICE_URL",
    "http://127.0.0.1:8000",
)


PAYMENT_SERVICE_URL = os.getenv(
    "PAYMENT_SERVICE_URL",
    "http://127.0.0.1:8001",
)


# Services SentinelAI monitors
SERVICES = {

    "order-service":
        f"{ORDER_SERVICE_URL}/health",

    "payment-service":
        f"{PAYMENT_SERVICE_URL}/health",
}


# ==========================================
# SENTINELAI HEALTH
# ==========================================

@app.get("/health")
def health():

    return {
        "status": "healthy"
    }


# ==========================================
# INCIDENT API
# ==========================================

@app.get("/api/anomalies")
def get_anomalies():

    try:
        init_incidents_table()
        incidents = list_incidents()
    except Exception:
        incidents = []


    # Count incidents that are still happening.
    active_count = sum(
        1
        for incident in incidents
        if incident.get("status") == "ACTIVE"
    )


    # Count incidents that SentinelAI detected
    # and later automatically resolved.
    resolved_count = sum(
        1
        for incident in incidents
        if incident.get("status") == "RESOLVED"
    )


    # We created some incidents before adding
    # the ACTIVE / RESOLVED lifecycle system.
    #
    # Keep them instead of deleting old data.
    legacy_count = sum(
        1
        for incident in incidents
        if incident.get("status")
        not in ("ACTIVE", "RESOLVED")
    )


    # Show newest incidents first.
    #
    # New incidents use started_at.
    # Older incidents only have timestamp,
    # so we support both formats.
    incidents.sort(
        key=lambda incident: (
            incident.get("started_at")
            or incident.get("timestamp")
            or ""
        ),
        reverse=True,
    )


    return {
        "count": len(incidents),
        "active_count": active_count,
        "resolved_count": resolved_count,
        "legacy_count": legacy_count,
        "incidents": incidents,
    }
    


# ==========================================
# METRICS API
# ==========================================

@app.get("/api/metrics")
def get_metrics():

    # Try the database first so the dashboard reflects the latest request data.
    try:
        init_metric_table()
        metrics = get_recent_metrics(limit=100)
    except Exception:
        metrics = []

    # Fallback to the legacy file format if the DB is unavailable.
    if not metrics and PAYMENT_METRICS_FILE.exists():
        with open(PAYMENT_METRICS_FILE, "r") as file:
            for line in file:
                if not line.strip():
                    continue
                try:
                    metric = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if metric.get("path") != "/payments":
                    continue
                metrics.append(
                    {
                        "timestamp": metric.get("timestamp"),
                        "service": metric.get("service"),
                        "latency_ms": metric.get("latency_ms"),
                        "status": metric.get("status"),
                        "status_code": metric.get("status"),
                        "mode": metric.get("mode", "unknown"),
                    }
                )

    recent_metrics = []
    for metric in metrics:
        if metric.get("path") != "/payments" and metric.get("status") is None:
            continue

        if metric.get("path") and metric.get("path") != "/payments":
            continue

        graph_metric = {
            "timestamp": metric.get("timestamp"),
            "service": metric.get("service"),
            "latency_ms": metric.get("latency_ms"),
            "status": metric.get("status_code") if metric.get("status_code") is not None else metric.get("status"),
            "mode": metric.get("mode", "unknown"),
        }
        recent_metrics.append(graph_metric)

    recent_metrics = recent_metrics[-100:]

    return {
        "count": len(recent_metrics),
        "metrics": recent_metrics,
    }


# ==========================================
# CHECK ONE SERVICE
# ==========================================

async def check_service(
    client,
    service_name,
    health_url,
):

    try:

        # Ask the service for its health
        response = await client.get(
            health_url,
            timeout=2.0,
        )


        # Health endpoint returned an error
        if response.status_code != 200:

            return {
                "name": service_name,
                "status": "DOWN",
            }


        health_data = response.json()


        # ==================================
        # PAYMENT SERVICE
        # ==================================

        if service_name == "payment-service":

            mode = health_data.get(
                "mode",
                "normal",
            )


            # Service is alive but slow
            if mode == "slow":

                return {
                    "name": service_name,
                    "status": "SLOW",
                    "mode": mode,
                }


            # Service is alive but payments fail
            if mode == "fail":

                return {
                    "name": service_name,
                    "status": "FAILING",
                    "mode": mode,
                }


            # Normal Payment Service
            return {
                "name": service_name,
                "status": "HEALTHY",
                "mode": mode,
            }


        # Order Service is healthy
        # if its health endpoint responded.
        return {
            "name": service_name,
            "status": "HEALTHY",
        }


    except httpx.RequestError:

        # Cannot connect to the service
        return {
            "name": service_name,
            "status": "DOWN",
        }


# ==========================================
# SERVICE HEALTH API
# ==========================================

@app.get("/api/services")
async def get_services():

    service_results = []


    # Reuse one HTTP client
    # for all service checks
    async with httpx.AsyncClient() as client:

        for (
            service_name,
            health_url,
        ) in SERVICES.items():

            result = await check_service(
                client,
                service_name,
                health_url,
            )


            service_results.append(
                result
            )


    # Number of monitored services
    total_services = len(
        service_results
    )


    # Number currently fully healthy
    healthy_services = sum(
        service["status"] == "HEALTHY"
        for service in service_results
    )


    return {
        "count": total_services,
        "healthy_count": healthy_services,
        "services": service_results,
    }
