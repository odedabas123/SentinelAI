"""Reproducible evaluation for SentinelAI's rolling-window detector."""

import argparse
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

from ml.window_detector import (
    aggregate_metrics_by_window,
    predict_window_anomaly,
    train_window_model,
)


TRAFFIC_TYPES = ("NORMAL", "SLOW", "FAILURE")
WINDOW_SECONDS = 60
DEFAULT_EVALUATION_SEEDS = (42, 43, 44, 45, 46)
LATENCY_SCENARIOS = (
    ("baseline", 1.0),
    ("mild", 1.25),
    ("moderate", 1.75),
    ("severe", 3.0),
    ("extreme", 9.0),
)
ERROR_SCENARIOS = (
    ("0%", 0.0),
    ("low", 1 / 12),
    ("moderate", 3 / 12),
    ("high", 6 / 12),
)


def generate_controlled_metrics(
    traffic_type: str,
    window_count: int = 20,
    window_seconds: int = WINDOW_SECONDS,
    seed: int = 42,
    start_time: datetime | None = None,
) -> List[Dict[str, Any]]:
    """Generate deterministic request metrics for one traffic class."""
    traffic_type = traffic_type.upper()
    if traffic_type not in TRAFFIC_TYPES:
        raise ValueError(f"Unsupported traffic type: {traffic_type}")
    if window_count < 1:
        raise ValueError("window_count must be at least 1")

    rng = random.Random(seed)
    base_time = start_time or datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    metrics: List[Dict[str, Any]] = []

    for window_index in range(window_count):
        window_start = base_time + timedelta(seconds=window_index * window_seconds)
        for request_index in range(12):
            timestamp = window_start + timedelta(seconds=2 + request_index * 4)
            status_code = 200

            if traffic_type == "NORMAL":
                latency_ms = max(1.0, rng.gauss(100.0, 5.0))
            elif traffic_type == "SLOW":
                latency_ms = max(1.0, rng.gauss(900.0, 40.0))
            else:
                status_code = 500 if request_index < 8 else 200
                latency_ms = max(
                    1.0,
                    rng.gauss(1600.0 if status_code >= 500 else 110.0, 35.0),
                )

            metrics.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "service": "payment-service",
                    "method": "POST",
                    "path": "/payments",
                    "status": status_code,
                    "status_code": status_code,
                    "latency_ms": round(latency_ms, 3),
                    "mode": traffic_type.lower(),
                }
            )

    return metrics


def generate_scenario_metrics(
    scenario_name: str,
    window_count: int = 20,
    window_seconds: int = WINDOW_SECONDS,
    seed: int = 42,
    latency_multiplier: float = 1.0,
    error_rate: float = 0.0,
    start_time: datetime | None = None,
) -> List[Dict[str, Any]]:
    """Generate deterministic windows with independently controlled degradation."""
    if window_count < 1:
        raise ValueError("window_count must be at least 1")
    if latency_multiplier <= 0:
        raise ValueError("latency_multiplier must be greater than 0")
    if not 0 <= error_rate <= 1:
        raise ValueError("error_rate must be between 0 and 1")

    rng = random.Random(seed)
    base_time = start_time or datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    metrics: List[Dict[str, Any]] = []
    error_count = round(12 * error_rate)

    for window_index in range(window_count):
        window_start = base_time + timedelta(seconds=window_index * window_seconds)
        for request_index in range(12):
            status_code = 500 if request_index < error_count else 200
            latency_ms = max(1.0, rng.gauss(100.0, 5.0) * latency_multiplier)
            metrics.append(
                {
                    "timestamp": (window_start + timedelta(seconds=2 + request_index * 4)).isoformat(),
                    "service": "payment-service",
                    "method": "POST",
                    "path": "/payments",
                    "status": status_code,
                    "status_code": status_code,
                    "latency_ms": round(latency_ms, 3),
                    "mode": scenario_name,
                }
            )

    return metrics


def _score_windows(model: Any, metrics: Iterable[Dict[str, Any]], window_seconds: int):
    windows = aggregate_metrics_by_window(metrics, window_seconds=window_seconds)
    results = []
    for window in windows:
        is_anomaly, score = predict_window_anomaly(model, window)
        results.append({"is_anomaly": is_anomaly, "score": score})
    return results


def evaluate_window_detector(
    window_count: int = 20,
    training_window_count: int = 40,
    calibration_window_count: int = 40,
    window_seconds: int = WINDOW_SECONDS,
    seed: int = 42,
) -> Dict[str, Dict[str, Any]]:
    """Calibrate on normal windows, then measure separate traffic datasets."""
    training_metrics = generate_controlled_metrics(
        "NORMAL",
        window_count=training_window_count,
        window_seconds=window_seconds,
        seed=seed,
    )
    calibration_metrics = generate_controlled_metrics(
        "NORMAL",
        window_count=calibration_window_count,
        window_seconds=window_seconds,
        seed=seed + 100,
        start_time=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    model = train_window_model(
        training_metrics,
        window_seconds=window_seconds,
        calibration_metrics=calibration_metrics,
        calibration_percentile=95.0,
    )

    evaluation_results: Dict[str, Dict[str, Any]] = {}
    for index, traffic_type in enumerate(TRAFFIC_TYPES):
        metrics = generate_controlled_metrics(
            traffic_type,
            window_count=window_count,
            window_seconds=window_seconds,
            seed=seed + index + 1,
            start_time=datetime(2026, 9, 2 + index, 12, 0, tzinfo=timezone.utc),
        )
        scores = _score_windows(model, metrics, window_seconds)
        anomalies = sum(1 for result in scores if result["is_anomaly"])
        total = len(scores)

        evaluation_results[traffic_type] = {
            "threshold": model.anomaly_threshold,
            "windows_tested": total,
            "anomalies_detected": anomalies,
            "detection_rate": anomalies / total if total else 0.0,
            "false_positive_rate": anomalies / total if traffic_type == "NORMAL" and total else 0.0,
            "false_positives": anomalies if traffic_type == "NORMAL" else 0,
            "false_negatives": total - anomalies if traffic_type != "NORMAL" else 0,
            "anomaly_scores": [result["score"] for result in scores],
            "average_anomaly_score": (
                sum(result["score"] for result in scores) / total if total else 0.0
            ),
        }

    return evaluation_results


def print_summary(results: Dict[str, Dict[str, Any]]) -> None:
    """Print compact results that are easy to compare between runs."""
    print("Rolling-window detector evaluation")
    print(f"calibrated threshold: {results['NORMAL']['threshold']:.4f} (95th percentile of normal calibration scores)")
    for traffic_type in TRAFFIC_TYPES:
        result = results[traffic_type]
        print(f"\n{traffic_type}")
        print(f"{result['windows_tested']} windows")
        print(f"{result['anomalies_detected']} detected anomalies")
        if traffic_type == "NORMAL":
            print(f"{result['false_positive_rate']:.1%} false-positive rate")
        else:
            print(f"{result['detection_rate']:.1%} detection rate")
        print(
            f"confusion: {result['false_positives']} false positives, "
            f"{result['false_negatives']} false negatives"
        )
        print(f"average anomaly score: {result['average_anomaly_score']:.4f}")
        print(
            "score range: "
            f"{min(result['anomaly_scores']):.4f} - "
            f"{max(result['anomaly_scores']):.4f}"
        )


def _summarize_scenario(scored_windows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize scores and aggregate features after all windows are scored."""
    total = len(scored_windows)
    anomalies = sum(1 for window in scored_windows if window["is_anomaly"])
    scores = [window["score"] for window in scored_windows]
    return {
        "windows_tested": total,
        "anomalies_detected": anomalies,
        "detection_rate": anomalies / total if total else 0.0,
        "average_anomaly_score": sum(scores) / total if total else 0.0,
        "score_range": (min(scores), max(scores)) if scores else (0.0, 0.0),
        "average_features": {
            feature: (
                sum(window["features"][feature] for window in scored_windows) / total
                if total else 0.0
            )
            for feature in ("average_latency_ms", "p95_latency_ms", "error_rate", "request_count")
        },
    }


def evaluate_robustness(
    window_count: int = 20,
    training_window_count: int = 40,
    calibration_window_count: int = 40,
    seeds: Iterable[int] = DEFAULT_EVALUATION_SEEDS,
    window_seconds: int = WINDOW_SECONDS,
) -> Dict[str, Any]:
    """Measure degradation scenarios using independent normal-only calibrations."""
    seeds = tuple(seeds)
    if not seeds:
        raise ValueError("At least one evaluation seed is required")

    latency_windows = {name: [] for name, _ in LATENCY_SCENARIOS}
    error_windows = {name: [] for name, _ in ERROR_SCENARIOS}
    thresholds = []

    for seed_index, seed in enumerate(seeds):
        training_metrics = generate_controlled_metrics(
            "NORMAL",
            window_count=training_window_count,
            window_seconds=window_seconds,
            seed=seed,
            start_time=datetime(2026, 9, 1 + seed_index, 12, 0, tzinfo=timezone.utc),
        )
        calibration_metrics = generate_controlled_metrics(
            "NORMAL",
            window_count=calibration_window_count,
            window_seconds=window_seconds,
            seed=seed + 100,
            start_time=datetime(2026, 9, 20 + seed_index, 12, 0, tzinfo=timezone.utc),
        )
        model = train_window_model(
            training_metrics,
            window_seconds=window_seconds,
            calibration_metrics=calibration_metrics,
            calibration_percentile=95.0,
        )
        thresholds.append(model.anomaly_threshold)

        for scenario_index, (scenario_name, multiplier) in enumerate(LATENCY_SCENARIOS):
            metrics = generate_scenario_metrics(
                scenario_name,
                window_count=window_count,
                window_seconds=window_seconds,
                seed=seed + 1000 + scenario_index,
                latency_multiplier=multiplier,
                start_time=datetime(2026, 10, 1 + seed_index, 12, 0, tzinfo=timezone.utc),
            )
            for window in aggregate_metrics_by_window(metrics, window_seconds=window_seconds):
                is_anomaly, score = predict_window_anomaly(model, window)
                latency_windows[scenario_name].append(
                    {"is_anomaly": is_anomaly, "score": score, "features": window}
                )

        for scenario_index, (scenario_name, error_rate) in enumerate(ERROR_SCENARIOS):
            metrics = generate_scenario_metrics(
                scenario_name,
                window_count=window_count,
                window_seconds=window_seconds,
                seed=seed + 2000 + scenario_index,
                error_rate=error_rate,
                start_time=datetime(2026, 11, 1 + seed_index, 12, 0, tzinfo=timezone.utc),
            )
            for window in aggregate_metrics_by_window(metrics, window_seconds=window_seconds):
                is_anomaly, score = predict_window_anomaly(model, window)
                error_windows[scenario_name].append(
                    {"is_anomaly": is_anomaly, "score": score, "features": window}
                )

    return {
        "seeds": list(seeds),
        "thresholds": thresholds,
        "average_threshold": sum(thresholds) / len(thresholds),
        "reliability_cutoff": 0.8,
        "latency": {name: _summarize_scenario(windows) for name, windows in latency_windows.items()},
        "error_rate": {name: _summarize_scenario(windows) for name, windows in error_windows.items()},
    }


def _first_reliable_scenario(results: Dict[str, Dict[str, Any]]) -> str:
    """Find the first scenario meeting the explicit 80% reporting criterion."""
    for name, result in results.items():
        if result["detection_rate"] >= 0.8:
            return name
    return "none"


def print_robustness_summary(results: Dict[str, Any]) -> None:
    """Print a compact operating-range report for latency and error degradation."""
    print("\nRolling-window robustness evaluation")
    print(f"seeds: {', '.join(str(seed) for seed in results['seeds'])}")
    print(
        f"calibrated threshold average: {results['average_threshold']:.4f} "
        "(each seed calibrated from NORMAL data only)"
    )

    for category, scenarios in (("LATENCY", results["latency"]), ("ERROR RATE", results["error_rate"])):
        print(f"\n{category}")
        for name, result in scenarios.items():
            features = result["average_features"]
            low, high = result["score_range"]
            print(
                f"{name:9} {result['windows_tested']:3} windows | "
                f"detected {result['detection_rate']:.1%} | "
                f"avg score {result['average_anomaly_score']:.4f} | "
                f"range {low:.4f}-{high:.4f} | "
                f"avg latency {features['average_latency_ms']:.1f} ms | "
                f"avg errors {features['error_rate']:.1%}"
            )
        print(f"first >=80% detection: {_first_reliable_scenario(scenarios)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", type=int, default=20, help="Evaluation windows per traffic class")
    parser.add_argument("--training-windows", type=int, default=40, help="Normal windows used for training")
    parser.add_argument("--calibration-windows", type=int, default=40, help="Normal windows used to calibrate the threshold")
    parser.add_argument("--seeds", default=",".join(str(seed) for seed in DEFAULT_EVALUATION_SEEDS), help="Comma-separated evaluation seeds")
    args = parser.parse_args()

    results = evaluate_robustness(
        window_count=args.windows,
        training_window_count=args.training_windows,
        calibration_window_count=args.calibration_windows,
        seeds=tuple(int(seed.strip()) for seed in args.seeds.split(",") if seed.strip()),
    )
    print_robustness_summary(results)


if __name__ == "__main__":
    main()