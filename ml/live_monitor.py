# Used to read and write JSON data
import json
import sys

# Used to pause briefly while waiting for new metrics
import time

# Used to create a unique ID for every incident
import uuid

# Lets us create safe file paths
from pathlib import Path

# NumPy helps us calculate statistical percentiles
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.incident_store import (
    get_active_incidents,
    get_incident_by_id,
    init_incidents_table,
    list_incidents,
    upsert_incident,
)
from database.metric_store import get_metrics_after_id, get_recent_metrics, init_metrics_table
from alerting import WebhookNotifier

# Import our reusable ML functions
from detector import (
    NORMAL_FILE,
    load_payment_latencies,
    train_model,
    predict_latency,
)
from ml.window_detector import (
    aggregate_metrics_by_window,
    load_jsonl_metrics,
    predict_window_anomaly,
    train_window_model,
)


# Folder containing this file
BASE_DIR = Path(__file__).resolve().parent


# Live metrics created by Payment Service
PAYMENT_METRICS_FILE = (
    BASE_DIR
    / ".."
    / "services"
    / "payment-service"
    / "metrics.jsonl"
).resolve()


# File containing SentinelAI incidents
ANOMALIES_FILE = (
    BASE_DIR
    / "data"
    / "anomalies.jsonl"
)


def read_metrics_from_jsonl(last_seen_id=0):
    """Fallback reader used only while the database is unavailable."""
    if not PAYMENT_METRICS_FILE.exists():
        return []

    metrics = []
    with open(PAYMENT_METRICS_FILE, "r") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                metric = json.loads(line)
            except json.JSONDecodeError:
                continue
            metrics.append({**metric, "id": last_seen_id + len(metrics) + 1})
    return metrics


# ==========================================
# ACTIVE INCIDENTS
# ==========================================

# This dictionary keeps track of problems that
# are currently happening.
#
# Example key:
#
# ("payment-service", "/payments", "LATENCY_ANOMALY")
#
# Example value:
#
# "a1b2c3..."
ACTIVE_INCIDENTS = {}
alert_notifier = WebhookNotifier.from_environment()


# ==========================================
# INCIDENT KEY
# ==========================================

def get_incident_key(
    metric,
    incident_type,
):

    # An incident is uniquely identified by:
    #
    # service + endpoint + problem type
    #
    # This prevents every slow request from
    # becoming a completely new incident.
    return (
        metric["service"],
        metric["path"],
        incident_type,
    )


# ==========================================
# READ INCIDENTS
# ==========================================

def load_incidents():

    try:
        init_incidents_table()
        return list_incidents()
    except Exception:
        # Fall back to the legacy JSONL file only if PostgreSQL is unavailable.
        if not ANOMALIES_FILE.exists():
            return []

        incidents = []
        with open(ANOMALIES_FILE, "r") as file:
            for line in file:
                try:
                    incident = json.loads(line)
                    incidents.append(incident)
                except json.JSONDecodeError:
                    continue
        return incidents


# ==========================================
# WRITE INCIDENTS
# ==========================================

def write_incidents(incidents):

    try:
        init_incidents_table()
        for incident in incidents:
            upsert_incident(incident)
        return
    except Exception:
        with open(ANOMALIES_FILE, "w") as file:
            for incident in incidents:
                file.write(json.dumps(incident) + "\n")


# ==========================================
# RESTORE ACTIVE INCIDENTS
# ==========================================

def restore_active_incidents():

    # Docker or SentinelAI may restart while
    # an incident is still active.
    #
    # We reload those incidents so the monitor
    # does not forget about them.
    try:
        init_incidents_table()
        incidents = get_active_incidents()
    except Exception:
        incidents = load_incidents()

    for incident in incidents:

        if incident.get("status") != "ACTIVE":
            continue

        if "incident_id" not in incident:
            continue

        key = (
            incident.get("service"),
            incident.get("path"),
            incident.get("incident_type"),
        )

        ACTIVE_INCIDENTS[key] = incident["incident_id"]


# ==========================================
# SAVE NEW INCIDENT
# ==========================================

def save_incident(
    metric,
    incident_type,
    severity,
):

    # Build a key representing this exact
    # kind of ongoing problem.
    key = get_incident_key(
        metric,
        incident_type,
    )


    # If this problem is already active,
    # do not create another incident.
    #
    # Instead, update the existing one.
    if key in ACTIVE_INCIDENTS:

        update_active_incident(
            ACTIVE_INCIDENTS[key],
            metric,
        )

        return False


    # Generate a unique ID.
    incident_id = str(
        uuid.uuid4()
    )


    # Build the incident that will be stored.
    incident = {

        # Unique identifier for this incident
        "incident_id": incident_id,

        # Lifecycle information
        "status": "ACTIVE",
        "started_at": metric["timestamp"],
        "resolved_at": None,

        # Timestamp of the most recent bad request
        "last_seen_at": metric["timestamp"],

        # Number of bad requests connected
        # to this incident
        "occurrence_count": 1,

        # Request information
        "timestamp": metric["timestamp"],
        "service": metric["service"],
        "method": metric["method"],
        "path": metric["path"],
        "status_code": metric["status"],
        "latency_ms": metric["latency_ms"],

        # What kind of problem SentinelAI detected
        "incident_type": incident_type,

        # How serious the problem is
        "severity": severity,
    }


    # Keep the development mode if it exists.
    #
    # This lets us see whether Payment Service
    # was in normal, slow, or fail mode.
    if "mode" in metric:

        incident["mode"] = (
            metric["mode"]
        )


    try:
        init_incidents_table()
        upsert_incident(incident)
    except Exception:
        with open(ANOMALIES_FILE, "a") as file:
            file.write(json.dumps(incident) + "\n")

    ACTIVE_INCIDENTS[key] = incident_id

    alert_notifier.send_new_incident(incident)


    return True


# ==========================================
# UPDATE ACTIVE INCIDENT
# ==========================================

def update_active_incident(
    incident_id,
    metric,
):

    incident = get_incident_by_id(incident_id)
    if not incident:
        return

    incident["status"] = "ACTIVE"
    incident["last_seen_at"] = metric["timestamp"]
    incident["occurrence_count"] = incident.get("occurrence_count", 1) + 1
    incident["latency_ms"] = metric["latency_ms"]
    incident["status_code"] = metric["status"]
    incident["timestamp"] = metric["timestamp"]

    if "mode" in metric:
        incident["mode"] = metric["mode"]

    try:
        init_incidents_table()
        upsert_incident(incident)
    except Exception:
        incidents = load_incidents()
        for existing in incidents:
            if existing.get("incident_id") == incident_id:
                existing.update(incident)
                break
        write_incidents(incidents)


# ==========================================
# RESOLVE INCIDENT
# ==========================================

def resolve_incident(
    metric,
    incident_type,
):

    key = get_incident_key(
        metric,
        incident_type,
    )

    if key not in ACTIVE_INCIDENTS:
        return False

    incident_id = ACTIVE_INCIDENTS[key]
    incident = None

    try:
        init_incidents_table()
        incident = get_incident_by_id(incident_id)
        if incident:
            incident["status"] = "RESOLVED"
            incident["resolved_at"] = metric["timestamp"]
            incident["last_seen_at"] = metric["timestamp"]
            upsert_incident(incident)
    except Exception:
        incidents = load_incidents()
        for stored_incident in incidents:
            if stored_incident.get("incident_id") == incident_id:
                incident = stored_incident
                incident["status"] = "RESOLVED"
                incident["resolved_at"] = metric["timestamp"]
                break
        write_incidents(incidents)

    del ACTIVE_INCIDENTS[key]
    if incident:
        alert_notifier.send_recovery(incident)
    return True


# ==========================================
# HEALTHY LATENCY THRESHOLD
# ==========================================

def calculate_latency_limit(normal_data):

    # Extract latency numbers from:
    #
    # [
    #     [101.2],
    #     [100.9],
    #     ...
    # ]
    latencies = [
        request[0]
        for request in normal_data
    ]


    # Find the 99th percentile of healthy traffic.
    percentile_99 = np.percentile(
        latencies,
        99,
    )


    # Add 20% headroom so harmless variations
    # do not become production incidents.
    latency_limit = (
        percentile_99 * 1.20
    )


    return latency_limit


# ==========================================
# ANALYZE ONE LIVE METRIC
# ==========================================

def analyze_metric(
    model,
    metric,
    latency_limit,
):

    # For now SentinelAI only analyzes
    # actual payment requests.
    if metric.get("path") != "/payments":
        return


    # Extract useful information.
    latency_ms = metric["latency_ms"]

    status_code = metric["status"]


    # ------------------------------------------
    # CASE 1:
    # HTTP FAILURE
    # ------------------------------------------

    # HTTP 500 and above means the service
    # returned a server-side error.
    if status_code >= 500:

        print(
            f"{metric['service']} "
            f"{metric['path']} "
            f"HTTP {status_code} "
            f"-> CRITICAL ERROR"
        )


        # Create a new incident only if this
        # failure is not already active.
        created = save_incident(
            metric,
            incident_type="HTTP_ERROR",
            severity="CRITICAL",
        )


        if created:

            print(
                "  -> New critical incident created"
            )

        else:

            print(
                "  -> Existing critical incident updated"
            )


        # We already know this request failed,
        # so there is no need to run latency ML.
        return


    # ------------------------------------------
    # HTTP RECOVERY
    # ------------------------------------------

    # If the request succeeded again,
    # an active HTTP error has recovered.
    resolved_http = resolve_incident(
        metric,
        incident_type="HTTP_ERROR",
    )


    if resolved_http:

        print(
            "  -> HTTP error incident resolved"
        )


    # ------------------------------------------
    # CASE 2:
    # ML LATENCY ANALYSIS
    # ------------------------------------------

    # Ask Isolation Forest whether this latency
    # looks unusual compared with healthy traffic.
    is_anomaly = predict_latency(
        model,
        latency_ms,
    )


    if is_anomaly:

        result = "ANOMALY"

    else:

        result = "NORMAL"


    print(
        f"{metric['service']} "
        f"{metric['path']} "
        f"{latency_ms:.2f} ms "
        f"-> {result}"
    )


    # ML says unusual AND latency is outside
    # our healthy safety range.
    if (
        is_anomaly
        and latency_ms > latency_limit
    ):

        # Create or update the ongoing
        # high-latency incident.
        created = save_incident(
            metric,
            incident_type="LATENCY_ANOMALY",
            severity="HIGH",
        )


        if created:

            print(
                "  -> New high latency incident created"
            )

        else:

            print(
                "  -> Existing latency incident updated"
            )


    else:

        # The request is healthy enough again.
        #
        # If we had an active latency incident,
        # mark it as resolved.
        resolved_latency = resolve_incident(
            metric,
            incident_type="LATENCY_ANOMALY",
        )


        if resolved_latency:

            print(
                "  -> Latency incident resolved"
            )


        # ML noticed something unusual,
        # but it is still inside our healthy
        # safety range.
        if is_anomaly:

            print(
                "  -> Unusual, but below incident threshold"
            )


# ==========================================
# START LIVE MONITOR
# ==========================================

def load_training_metrics_for_window_model(limit: int = 200):
    """Prefer recent PostgreSQL metrics so the rolling-window model reflects current behavior."""
    try:
        init_metrics_table()
        metrics = get_recent_metrics(limit=limit)
        pay_metrics = [
            metric for metric in metrics
            if metric.get("path") == "/payments" and (metric.get("status_code") is None or metric.get("status_code") < 400)
        ]
        if pay_metrics:
            return pay_metrics
    except Exception:
        pass

    return load_jsonl_metrics(NORMAL_FILE)


def analyze_window_anomaly(window_model, metrics, window_seconds=60):
    """Score service-level rolling windows and create a single incident per active service/path window."""
    if window_model is None or not metrics:
        return

    windows = aggregate_metrics_by_window(metrics, window_seconds=window_seconds)
    if not windows:
        return

    for window in windows:
        if window.get("request_count", 0) < 3:
            continue

        is_anomaly, score = predict_window_anomaly(window_model, window)
        if not is_anomaly:
            key = (window.get("service"), window.get("path"), "WINDOW_ANOMALY")
            if key in ACTIVE_INCIDENTS:
                metric = {
                    "timestamp": window.get("window_end") or window.get("window_start"),
                    "service": window.get("service"),
                    "method": "POST",
                    "path": window.get("path"),
                    "status": 200,
                    "status_code": 200,
                    "latency_ms": window.get("average_latency_ms", 0),
                }
                resolve_incident(metric, "WINDOW_ANOMALY")
            continue

        if window.get("error_rate", 0) > 0.2:
            severity = "CRITICAL"
        elif window.get("average_latency_ms", 0) > 300:
            severity = "HIGH"
        else:
            severity = "MEDIUM"

        key = (window.get("service"), window.get("path"), "WINDOW_ANOMALY")
        metric = {
            "timestamp": window.get("window_end") or window.get("window_start"),
            "service": window.get("service"),
            "method": "POST",
            "path": window.get("path"),
            "status": 500 if window.get("error_rate", 0) > 0.2 else 200,
            "status_code": 500 if window.get("error_rate", 0) > 0.2 else 200,
            "latency_ms": window.get("average_latency_ms", 0),
            "mode": "window-scan",
        }

        if key in ACTIVE_INCIDENTS:
            update_active_incident(ACTIVE_INCIDENTS[key], metric)
            continue

        print(
            f"{window.get('service')} {window.get('path')} "
            f"window anomaly -> score={score:.3f} avg_latency={window.get('average_latency_ms', 0):.1f} ms"
        )
        save_incident(metric, incident_type="WINDOW_ANOMALY", severity=severity)


def start_monitor():

    print(
        "Training SentinelAI model..."
    )


    # Load historical healthy traffic.
    normal_data = load_payment_latencies(
        NORMAL_FILE
    )


    # Train the original single-request Isolation Forest.
    model = train_model(
        normal_data
    )

    # Build a second model for rolling-window anomaly scoring.
    window_training_data = load_training_metrics_for_window_model(limit=200)
    window_model = None
    if window_training_data:
        try:
            window_model = train_window_model(window_training_data, window_seconds=60)
        except ValueError:
            window_model = None


    # Build our dynamic latency threshold.
    latency_limit = calculate_latency_limit(
        normal_data
    )


    # Restore incidents that were still active
    # before SentinelAI restarted.
    restore_active_incidents()


    print(
        f"Model trained on "
        f"{len(normal_data)} normal requests."
    )


    if window_model is not None:
        print("Window anomaly model trained from recent PostgreSQL metrics.")
    else:
        print("Window anomaly model available only in legacy fallback mode.")


    print(
        f"Incident latency threshold: "
        f"{latency_limit:.2f} ms"
    )


    print(
        f"Restored "
        f"{len(ACTIVE_INCIDENTS)} "
        f"active incidents."
    )


    print()


    print(
        "SentinelAI live monitor started."
    )


    print(
        f"Watching: {PAYMENT_METRICS_FILE}"
    )


    print(
        "Waiting for new payment requests..."
    )


    print()


    last_seen_metric_id = 0

    # Keep SentinelAI running continuously.
    while True:

        try:
            init_metrics_table()
            metrics = get_metrics_after_id(last_seen_metric_id, limit=100)
        except Exception:
            metrics = read_metrics_from_jsonl(last_seen_metric_id)

        if not metrics:
            time.sleep(0.1)
            continue

        for metric in metrics:
            analyze_metric(
                model,
                metric,
                latency_limit,
            )
            last_seen_metric_id = max(last_seen_metric_id, int(metric.get("id", 0)))

        try:
            init_metrics_table()
            all_recent_metrics = get_recent_metrics(limit=500)
        except Exception:
            all_recent_metrics = read_metrics_from_jsonl(0)

        if all_recent_metrics:
            analyze_window_anomaly(window_model, all_recent_metrics, window_seconds=60)


# Run the monitor when we execute:
#
# python live_monitor.py
if __name__ == "__main__":
    start_monitor()