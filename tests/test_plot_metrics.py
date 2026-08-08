from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import plot_metrics


def sample_report() -> dict:
    metrics = {
        "docker_cpu_percent": {"average": 10.0, "maximum": 20.0},
        "docker_memory_percent": {"average": 50.0, "maximum": 55.0},
        "elasticsearch_jvm_heap_used_bytes": {"average": 1000.0, "maximum": 1200.0},
    }
    return {
        "status": "passed",
        "benchmark_type": "baseline_single_client_search",
        "execution": "baseline",
        "index": "test-index",
        "protocol": {"client_count": 1},
        "runtime": {
            "index": {
                "name": "test-index",
                "document_count": 50_000,
                "store_size_bytes": 123_456,
            }
        },
        "system_metrics": metrics,
        "scenarios": [
            {
                "query_id": "example",
                "type": "keyword",
                "measured_request_count": 2,
                "successful_request_count": 2,
                "error_count": 0,
                "error_rate": 0.0,
                "measurement_duration_seconds": 0.03,
                "average_latency_ms": 15.0,
                "p95_latency_ms": 20.0,
                "throughput_requests_per_second": 66.666667,
                "result_count": 7,
                "system_metrics": metrics,
                "measurements": [
                    {"success": True, "client_latency_ms": 10.0},
                    {"success": True, "client_latency_ms": 20.0},
                ],
            }
        ],
    }


def test_normalization_recalculates_metrics_from_raw_requests() -> None:
    report = sample_report()
    rows = plot_metrics.normalize_report(report, Path("sample.json"))

    assert len(rows) == 1
    row = rows[0]
    assert row["average_latency_ms"] == 15.0
    assert row["p95_latency_ms"] == 20.0
    assert row["user_facing_throughput_per_second"] == pytest.approx(66.666667)
    assert row["internal_elasticsearch_request_throughput_per_second"] == pytest.approx(
        66.666667
    )
    assert row["error_rate"] == 0.0
    assert row["document_count"] == 50_000
    assert row["index_store_size_bytes"] == 123_456
    assert row["docker_cpu_average_percent"] == 10.0
    assert row["docker_memory_maximum_percent"] == 55.0


def test_future_benchmark_names_are_accepted_by_operational_schema(tmp_path: Path) -> None:
    report = sample_report()
    report["benchmark_type"] = "stage9_custom_optimized_run"
    path = tmp_path / "future.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    loaded = plot_metrics.load_report(path)
    assert loaded["benchmark_type"] == "stage9_custom_optimized_run"


def test_normalization_rejects_summary_that_disagrees_with_raw_requests() -> None:
    report = sample_report()
    report["scenarios"][0]["average_latency_ms"] = 999

    with pytest.raises(plot_metrics.MetricsError, match="average_latency_ms mismatch"):
        plot_metrics.normalize_report(report, Path("sample.json"))


def test_error_rate_uses_all_measured_requests() -> None:
    report = sample_report()
    scenario = report["scenarios"][0]
    scenario["measurements"][1] = {"success": False, "client_latency_ms": 20.0}
    scenario.update(
        {
            "successful_request_count": 1,
            "error_count": 1,
            "error_rate": 0.5,
            "average_latency_ms": 10.0,
            "p95_latency_ms": 10.0,
            "throughput_requests_per_second": 33.333333,
        }
    )

    row = plot_metrics.normalize_report(report, Path("sample.json"))[0]
    assert row["successful_request_count"] == 1
    assert row["error_count"] == 1
    assert row["error_rate"] == 0.5


def test_source_csv_is_checked_against_normalized_json(tmp_path: Path) -> None:
    report = sample_report()
    normalized = plot_metrics.normalize_report(report, Path("sample.json"))
    path = tmp_path / "source.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "query_id",
                "type",
                "result_count",
                "average_latency_ms",
                "p95_latency_ms",
                "error_rate",
                "throughput_requests_per_second",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(
            {
                "query_id": "example",
                "type": "keyword",
                "result_count": 7,
                "average_latency_ms": 15,
                "p95_latency_ms": 20,
                "error_rate": 0,
                "throughput_requests_per_second": 66.666667,
            }
        )

    validation = plot_metrics.validate_source_csv(report, normalized, path)
    assert validation["status"] == "passed"
    assert validation["row_count"] == 1


def test_markdown_contains_required_operational_metrics(tmp_path: Path) -> None:
    rows = plot_metrics.normalize_report(sample_report(), Path("sample.json"))
    path = tmp_path / "summary.md"
    plot_metrics.write_markdown(path, rows)
    text = path.read_text(encoding="utf-8")

    assert "Avg ms" in text
    assert "P95 ms" in text
    assert "User QPS" in text
    assert "Error rate" in text
    assert "Index MiB" in text
    assert "CPU avg/max" in text
    assert "Memory avg/max" in text


def test_defaults_include_stage5_and_stage6_artifacts() -> None:
    args = plot_metrics.parse_args([])
    assert args.inputs is None
    assert plot_metrics.DEFAULT_INPUTS == [
        Path("results/search_baseline.json"),
        Path("results/hybrid_comparison.json"),
        Path("results/load_test_baseline.json"),
    ]
    assert args.json_output == Path("results/metrics_summary.json")
    assert args.dashboard == Path("results/metrics_dashboard.png")
