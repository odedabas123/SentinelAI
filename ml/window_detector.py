# Window-level anomaly detection for SentinelAI.
#
# The existing detector still looks at one request at a time. This module adds
# a second layer that groups recent service metrics into rolling time windows and
# scores the aggregate behavior instead of a single latency spike.

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
from sklearn.ensemble import IsolationForest

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
NORMAL_FILE = DATA_DIR / "normal_payments.jsonl"
SLOW_FILE = DATA_DIR / "slow_payments.jsonl"


def _coerce_datetime(value):
    """Normalize ISO timestamps from JSONL / PostgreSQL into Python datetimes."""
    if value in (None, ""):
        return None

    if isinstance(value, str):
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    return value


def _window_start_for(timestamp: datetime, window_seconds: int) -> datetime:
    """Round the timestamp down to the nearest fixed-size time bucket."""
    seconds = int(timestamp.timestamp())
    bucket_seconds = (seconds // window_seconds) * window_seconds
    return datetime.fromtimestamp(bucket_seconds, tz=timestamp.tzinfo or timezone.utc)


def aggregate_metrics_by_window(
    metrics: Iterable[Dict[str, Any]],
    window_seconds: int = 60,
    service_name: str | None = None,
    path_filter: str | None = None,
) -> List[Dict[str, Any]]:
    """Group request metrics into service/path windows and compute aggregate features."""
    buckets = defaultdict(list)

    for metric in metrics:
        if metric is None:
            continue

        timestamp = _coerce_datetime(metric.get("timestamp"))
        if timestamp is None:
            continue

        service = metric.get("service") or "unknown-service"
        path = metric.get("path") or "unknown-path"

        if service_name and service != service_name:
            continue
        if path_filter and path != path_filter:
            continue

        bucket = _window_start_for(timestamp, window_seconds)
        bucket_key = (service, path, bucket)
        buckets[bucket_key].append(metric)

    window_rows: List[Dict[str, Any]] = []

    for (service, path, bucket_start), bucket_metrics in sorted(buckets.items(), key=lambda item: item[0][2]):
        latencies = [
            float(metric.get("latency_ms") or 0.0)
            for metric in bucket_metrics
            if metric.get("latency_ms") is not None
        ]

        if not latencies:
            continue

        status_codes = [
            int(metric.get("status_code") if metric.get("status_code") is not None else metric.get("status") or 0)
            for metric in bucket_metrics
        ]
        error_count = sum(1 for status in status_codes if status >= 400)
        request_count = len(bucket_metrics)
        window_seconds_float = max(float(window_seconds), 1.0)

        feature_row = {
            "service": service,
            "path": path,
            "window_start": bucket_start.isoformat(),
            "window_end": (bucket_start + timedelta(seconds=window_seconds)).isoformat(),
            "request_count": request_count,
            "request_rate_per_second": request_count / window_seconds_float,
            "average_latency_ms": float(np.mean(latencies)),
            "p95_latency_ms": float(np.percentile(latencies, 95)),
            "p99_latency_ms": float(np.percentile(latencies, 99)),
            "error_rate": error_count / request_count,
            "latency_std_dev_ms": float(np.std(latencies)),
            "max_latency_ms": float(np.max(latencies)),
        }
        window_rows.append(feature_row)

    return window_rows


def feature_vector(feature_row: Dict[str, Any]) -> List[float]:
    """Convert one window-level feature row into the ML input vector."""
    return [
        float(feature_row.get("request_count", 0.0)),
        float(feature_row.get("request_rate_per_second", 0.0)),
        float(feature_row.get("average_latency_ms", 0.0)),
        float(feature_row.get("p95_latency_ms", 0.0)),
        float(feature_row.get("p99_latency_ms", 0.0)),
        float(feature_row.get("error_rate", 0.0)),
        float(feature_row.get("latency_std_dev_ms", 0.0)),
        float(feature_row.get("max_latency_ms", 0.0)),
    ]


@dataclass
class WindowAnomalyModel:
    """Isolation Forest plus the normal-only threshold used by SentinelAI."""

    estimator: IsolationForest
    anomaly_threshold: float
    calibration_percentile: float
    training_window_count: int
    calibration_window_count: int

    def score_samples(self, vectors):
        """Delegate scoring so existing callers can treat this like the estimator."""
        return self.estimator.score_samples(vectors)


def train_window_model(
    normal_metrics: Iterable[Dict[str, Any]],
    window_seconds: int = 60,
    calibration_metrics: Iterable[Dict[str, Any]] | None = None,
    calibration_percentile: float = 95.0,
) -> WindowAnomalyModel:
    """Train on normal windows and calibrate the anomaly score threshold.

    IsolationForest.score_samples returns larger values for more normal samples.
    decision_function is score_samples minus the fitted offset: positive values
    indicate inliers and negative values indicate outliers. SentinelAI negates
    score_samples, so larger SentinelAI scores are more anomalous.
    The threshold is the requested percentile of normal calibration scores. When
    no separate calibration set is supplied, training windows are used as a
    backwards-compatible production fallback.
    """
    if not 0 < calibration_percentile <= 100:
        raise ValueError("calibration_percentile must be between 0 and 100")

    normal_metrics = list(normal_metrics)
    normal_windows = aggregate_metrics_by_window(normal_metrics, window_seconds=window_seconds)
    if not normal_windows:
        raise ValueError("No normal metrics were available to train the window model.")

    model = IsolationForest(
        contamination=0.05,
        random_state=42,
    )
    model.fit([feature_vector(window) for window in normal_windows])

    calibration_source = calibration_metrics if calibration_metrics is not None else normal_metrics
    calibration_windows = aggregate_metrics_by_window(
        calibration_source,
        window_seconds=window_seconds,
    )
    if not calibration_windows:
        raise ValueError("No normal metrics were available to calibrate the window model.")

    calibration_scores = [
        -float(model.score_samples([feature_vector(window)])[0])
        for window in calibration_windows
    ]
    anomaly_threshold = float(np.percentile(calibration_scores, calibration_percentile))

    return WindowAnomalyModel(
        estimator=model,
        anomaly_threshold=anomaly_threshold,
        calibration_percentile=calibration_percentile,
        training_window_count=len(normal_windows),
        calibration_window_count=len(calibration_windows),
    )


def predict_window_anomaly(model, feature_row: Dict[str, Any]) -> tuple[bool, float]:
    """Return whether a window exceeds the calibrated anomaly score threshold."""
    vector = [feature_vector(feature_row)]
    # score_samples is higher for normal samples. Negating it makes larger
    # SentinelAI scores consistently mean more anomalous behavior.
    anomaly_score = -float(model.score_samples(vector)[0])
    is_anomaly = anomaly_score > model.anomaly_threshold
    return is_anomaly, anomaly_score


def load_jsonl_metrics(file_path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file with metric records into a Python list."""
    metrics: List[Dict[str, Any]] = []
    if not file_path.exists():
        return metrics

    with open(file_path, "r") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                metrics.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return metrics


def run_window_demo():
    """Create small controlled normal, slow, and failure windows and print the score results."""
    normal_metrics = []
    slow_metrics = []
    failure_metrics = []

    base_time = datetime(2026, 8, 31, 12, 0, 0)

    for offset in range(12):
        normal_metrics.append({
            "timestamp": (base_time + timedelta(seconds=offset * 5)).isoformat(),
            "service": "payment-service",
            "path": "/payments",
            "status": 200,
            "status_code": 200,
            "latency_ms": 95 + (offset % 5),
            "method": "POST",
        })

    for offset in range(10):
        slow_metrics.append({
            "timestamp": (base_time + timedelta(seconds=offset * 3)).isoformat(),
            "service": "payment-service",
            "path": "/payments",
            "status": 200,
            "status_code": 200,
            "latency_ms": 2800 + offset,
            "method": "POST",
        })

    for offset in range(8):
        failure_metrics.append({
            "timestamp": (base_time + timedelta(seconds=offset * 4)).isoformat(),
            "service": "payment-service",
            "path": "/payments",
            "status": 500,
            "status_code": 500,
            "latency_ms": 1300 + offset * 80,
            "method": "POST",
        })

    model = train_window_model(normal_metrics, window_seconds=60)
    datasets = {
        "normal": aggregate_metrics_by_window(normal_metrics, window_seconds=60),
        "slow": aggregate_metrics_by_window(slow_metrics, window_seconds=60),
        "failure": aggregate_metrics_by_window(failure_metrics, window_seconds=60),
    }

    print("Window anomaly demo")
    for label, rows in datasets.items():
        row = rows[0] if rows else {}
        is_anomaly, score = predict_window_anomaly(model, row) if row else (False, 0.0)
        print(f"{label:7} -> anomaly={is_anomaly} score={score:.3f} features={row.get('request_count')} req / {row.get('average_latency_ms', 0):.1f} ms")


if __name__ == "__main__":
    run_window_demo()
