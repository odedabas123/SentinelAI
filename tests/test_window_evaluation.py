import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.evaluate_window_detector import (
    evaluate_robustness,
    evaluate_window_detector,
    generate_controlled_metrics,
    generate_scenario_metrics,
)
from ml.window_detector import aggregate_metrics_by_window


def test_controlled_traffic_has_expected_window_features():
    normal = generate_controlled_metrics("NORMAL", window_count=3, seed=7)
    slow = generate_controlled_metrics("SLOW", window_count=3, seed=7)
    failure = generate_controlled_metrics("FAILURE", window_count=3, seed=7)

    normal_window = aggregate_metrics_by_window(normal)[0]
    slow_window = aggregate_metrics_by_window(slow)[0]
    failure_window = aggregate_metrics_by_window(failure)[0]

    assert len(aggregate_metrics_by_window(normal)) == 3
    assert normal_window["error_rate"] == 0
    assert slow_window["average_latency_ms"] > normal_window["average_latency_ms"]
    assert failure_window["error_rate"] == 8 / 12


def test_evaluation_is_reproducible_and_reports_all_classes():
    first = evaluate_window_detector(
        window_count=4,
        training_window_count=8,
        calibration_window_count=8,
        seed=11,
    )
    second = evaluate_window_detector(
        window_count=4,
        training_window_count=8,
        calibration_window_count=8,
        seed=11,
    )

    assert first == second
    assert set(first) == {"NORMAL", "SLOW", "FAILURE"}
    for result in first.values():
        assert result["windows_tested"] == 4
        assert len(result["anomaly_scores"]) == 4
        assert 0.0 <= result["detection_rate"] <= 1.0
        assert result["threshold"] > 0


def test_robustness_evaluation_keeps_scenarios_separate_and_reproducible():
    first = evaluate_robustness(
        window_count=3,
        training_window_count=8,
        calibration_window_count=8,
        seeds=(21, 22),
    )
    second = evaluate_robustness(
        window_count=3,
        training_window_count=8,
        calibration_window_count=8,
        seeds=(21, 22),
    )

    assert first == second
    assert first["seeds"] == [21, 22]
    assert first["latency"]["baseline"]["windows_tested"] == 6
    assert first["error_rate"]["high"]["average_features"]["error_rate"] == 0.5
    assert generate_scenario_metrics("mild", window_count=1)[0]["mode"] == "mild"