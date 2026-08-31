# Used to simulate payment processing time
import asyncio

# Used to measure how long requests take
import time

# Used to convert Python dictionaries into JSON text
import json
import sys

# Lets us create safe file paths
from pathlib import Path

# Used to save the exact time of each request
from datetime import datetime, timezone

# Literal lets us allow only specific mode names
from typing import Literal

# FastAPI = our web framework
# Request = information about incoming HTTP requests
# HTTPException = lets us return HTTP errors such as 500
from fastapi import FastAPI, Request, HTTPException

# BaseModel lets us define the JSON we expect
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.metric_store import init_metrics_table, insert_metric


# Create the Payment Service
app = FastAPI(title="SentinelAI Payment Service")


# Find the folder where this main.py file is located
BASE_DIR = Path(__file__).resolve().parent


# Always save metrics inside the payment-service folder
METRICS_FILE = BASE_DIR / "metrics.jsonl"


# The service starts in normal mode
#
# Later this value can become:
# "normal"
# "slow"
# "fail"
payment_mode = "normal"


def persist_metric(metric):
    """Write a metric to PostgreSQL first and keep JSONL as a debug fallback."""
    try:
        init_metrics_table()
        insert_metric(metric)
    except Exception:
        with open(METRICS_FILE, "a") as file:
            file.write(json.dumps(metric) + "\n")


# This middleware runs automatically for every HTTP request
@app.middleware("http")
async def collect_metrics(request: Request, call_next):

    # Start measuring how long the request takes
    start_time = time.perf_counter()

    # Allow FastAPI to run the real endpoint
    response = await call_next(request)

    # Calculate total request time in milliseconds
    latency_ms = (time.perf_counter() - start_time) * 1000

    # Create one metric describing this request
    metric = {

        # When the request happened
        "timestamp": datetime.now(timezone.utc).isoformat(),

        # Which service handled it
        "service": "payment-service",

        # GET, POST, etc.
        "method": request.method,

        # Example: /payments
        "path": request.url.path,

        # Example: 200 or 500
        "status": response.status_code,

        # How long the request took
        "latency_ms": round(latency_ms, 2),

        # Also record which fault mode was active
        "mode": payment_mode,
    }

    persist_metric(metric)

    # Return the original HTTP response
    return response


# Defines the JSON required for a payment
class PaymentRequest(BaseModel):

    # ID of the order being paid
    order_id: int

    # How much money the payment is for
    amount: float


# Defines the JSON used to change Payment Service mode
class ModeRequest(BaseModel):

    # Only these three strings are allowed
    mode: Literal["normal", "slow", "fail"]


# Check if Payment Service is alive
@app.get("/health")
def health():

    # Also show which mode is currently active
    return {
        "status": "healthy",
        "mode": payment_mode,
    }


# Change the behavior of the Payment Service
@app.post("/mode")
def change_mode(mode_request: ModeRequest):

    # We want to modify the global payment_mode variable
    global payment_mode

    # Change the current mode
    payment_mode = mode_request.mode

    # Tell the user what mode is now active
    return {
        "message": "Payment mode changed",
        "mode": payment_mode,
    }


# Handle an actual payment
@app.post("/payments")
async def create_payment(payment: PaymentRequest):

    # NORMAL MODE
    # Simulate a healthy payment taking about 100 ms
    if payment_mode == "normal":
        await asyncio.sleep(0.1)


    # SLOW MODE
    # Simulate a dependency/service becoming extremely slow
    elif payment_mode == "slow":
        await asyncio.sleep(3.0)


    # FAIL MODE
    # Simulate the Payment Service crashing/failing
    elif payment_mode == "fail":
        raise HTTPException(
            status_code=500,
            detail="Simulated payment failure",
        )


    # If we reached here, payment succeeded
    return {
        "status": "success",
        "order_id": payment.order_id,
        "amount": payment.amount,
        "mode": payment_mode,
    }