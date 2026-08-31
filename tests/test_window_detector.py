from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.window_detector import (
    aggregate_metrics_by_window,
    feature_vector,
    predict_window_anomaly,
    train_window_model,
)
from ml.evaluate_window_detector import generate_controlled_metrics


def build_normal_window_metrics(window_count=1):
    base = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    metrics = []
    for window_index in range(window_count):
        window_start = base + timedelta(seconds=window_index * 60)
        for offset in range(12):
            metrics.append(
                {
                    "timestamp": (window_start + timedelta(seconds=offset * 5)).isoformat(),
                    "service": "payment-service",
                    "path": "/payments",
                    "status": 200,
                    "status_code": 200,
                    "latency_ms": 95 + (offset % 5),
                    "method": "POST",
                }
            )
    return metrics


def build_slow_window_metrics():
    base = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    metrics = []
    for offset in range(10):
        metrics.append(
            {
                "timestamp": (base + timedelta(seconds=offset * 3)).isoformat(),
                "service": "payment-service",
                "path": "/payments",
                "status": 200,
                "status_code": 200,
                "latency_ms": 2800 + offset,
                "method": "POST",
            }
        )
    return metrics


def test_window_feature_aggregation_builds_expected_stats():
    metrics = build_normal_window_metrics()
    windows = aggregate_metrics_by_window(metrics, window_seconds=60)

    assert len(windows) == 1
    window = windows[0]

    assert window["service"] == "payment-service"
    assert window["path"] == "/payments"
    assert window["request_count"] == 12
    assert window["average_latency_ms"] > 95
    assert window["p95_latency_ms"] > window["average_latency_ms"]
    assert window["error_rate"] == 0
    assert window["max_latency_ms"] >= window["average_latency_ms"]


def test_window_model_flags_slow_windows_as_anomalous():
    normal_metrics = generate_controlled_metrics("NORMAL", window_count=12, seed=12)
    calibration_metrics = generate_controlled_metrics("NORMAL", window_count=12, seed=13)
    slow_metrics = generate_controlled_metrics("SLOW", window_count=1, seed=14)

    model = train_window_model(
        normal_metrics,
        window_seconds=60,
        calibration_metrics=calibration_metrics,
    )
    slow_window = aggregate_metrics_by_window(slow_metrics, window_seconds=60)[0]

    is_anomaly, score = predict_window_anomaly(model, slow_window)

    assert is_anomaly is True
    assert score > 0
    assert len(feature_vector(slow_window)) == 8


def test_window_model_calibrates_threshold_from_normal_windows_only():
    training_metrics = build_normal_window_metrics()
    calibration_metrics = build_normal_window_metrics()

    model = train_window_model(
        training_metrics,
        window_seconds=60,
        calibration_metrics=calibration_metrics,
        calibration_percentile=95,
    )

    calibration_window = aggregate_metrics_by_window(calibration_metrics, window_seconds=60)[0]
    _, calibration_score = predict_window_anomaly(model, calibration_window)

    assert model.calibration_percentile == 95
    assert model.training_window_count == 1
    assert model.calibration_window_count == 1
    assert model.anomaly_threshold == calibration_score
