# Used to measure how long each request takes
import time

# Used to convert Python dictionaries into JSON text
import json

# Lets us work with safe file paths
from pathlib import Path

# Used to save the exact time each request happened
from datetime import datetime, timezone

# Lets us read configuration values from environment variables
import os
import sys

# httpx lets this service call another service over HTTP
import httpx

# FastAPI = web framework
# HTTPException = lets us return errors like HTTP 500
# Request = gives us information about the incoming request
from fastapi import FastAPI, HTTPException, Request

# BaseModel lets us define what JSON data we expect
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.metric_store import init_metrics_table, insert_metric


# ==========================================
# CREATE ORDER SERVICE
# ==========================================

app = FastAPI(
    title="SentinelAI Order Service"
)


# ==========================================
# FILE PATHS
# ==========================================

# Get the folder where this main.py file is located
BASE_DIR = Path(__file__).resolve().parent


# Save Order Service metrics here
METRICS_FILE = (
    BASE_DIR
    / "metrics.jsonl"
)


def persist_metric(metric):
    """Write a metric to PostgreSQL first and keep JSONL as a debug fallback."""
    try:
        init_metrics_table()
        insert_metric(metric)
    except Exception:
        with open(METRICS_FILE, "a") as file:
            file.write(json.dumps(metric) + "\n")


# ==========================================
# PAYMENT SERVICE CONFIGURATION
# ==========================================

# Address of the Payment Service.
#
# When we run SentinelAI manually,
# this defaults to localhost.
#
# When we run with Docker Compose,
# Docker will set:
#
# PAYMENT_SERVICE_URL=http://payment-service:8001
#
# This lets the same code work both
# inside and outside Docker.
PAYMENT_SERVICE_URL = os.getenv(
    "PAYMENT_SERVICE_URL",
    "http://127.0.0.1:8001",
)


# ==========================================
# METRICS MIDDLEWARE
# ==========================================

# This middleware automatically runs
# for EVERY request received by Order Service.
@app.middleware("http")
async def collect_metrics(
    request: Request,
    call_next,
):

    # Start measuring request duration
    start_time = time.perf_counter()


    # Let FastAPI process the real endpoint
    response = await call_next(
        request
    )


    # Calculate request latency
    # and convert seconds into milliseconds
    latency_ms = (
        time.perf_counter()
        - start_time
    ) * 1000


    # Create one metric record
    metric = {

        # Exact UTC time of the request
        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),

        # Which service handled it
        "service":
            "order-service",

        # GET / POST / etc.
        "method":
            request.method,

        # Example:
        # /orders
        "path":
            request.url.path,

        # HTTP response code
        "status":
            response.status_code,

        # Total request duration
        "latency_ms":
            round(
                latency_ms,
                2,
            ),
    }


    persist_metric(metric)

    # Return the real HTTP response
    return response


# ==========================================
# REQUEST MODEL
# ==========================================

# Defines the JSON expected by:
#
# POST /orders
class OrderRequest(BaseModel):

    # Unique order ID
    order_id: int

    # Payment amount
    amount: float


# ==========================================
# HEALTH ENDPOINT
# ==========================================

@app.get("/health")
def health():

    return {
        "status": "healthy"
    }


# ==========================================
# CREATE ORDER
# ==========================================

@app.post("/orders")
async def create_order(
    order: OrderRequest
):

    # Build the request that will be
    # sent to Payment Service
    payment_data = {

        "order_id":
            order.order_id,

        "amount":
            order.amount,
    }


    try:

        # Create an asynchronous HTTP client
        async with httpx.AsyncClient() as client:

            # Send the payment request
            # to Payment Service.
            payment_response = await client.post(

                f"{PAYMENT_SERVICE_URL}/payments",

                json=payment_data,

                # Do not wait forever
                # if Payment Service freezes.
                timeout=5.0,
            )


    # This happens when Payment Service
    # cannot be reached or times out.
    except httpx.RequestError:

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not connect "
                "to Payment Service"
            ),
        )


    # Payment Service answered,
    # but the payment itself failed.
    if payment_response.status_code != 200:

        raise HTTPException(
            status_code=500,
            detail="Payment Service failed",
        )


    # Convert Payment Service response
    # into a Python dictionary
    payment_result = (
        payment_response.json()
    )


    # Return the completed order
    return {

        "status":
            "order_created",

        "order_id":
            order.order_id,

        "payment":
            payment_result,
    }
