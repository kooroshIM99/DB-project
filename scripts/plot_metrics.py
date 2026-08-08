#!/usr/bin/env python3
"""Validate benchmark artifacts and generate reusable stage-7 tables/charts."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark
except ModuleNotFoundError:  # Direct execution from scripts/.
    import benchmark  # type: ignore[no-redef]


DEFAULT_INPUTS = [
    Path("results/search_baseline.json"),
    Path("results/hybrid_comparison.json"),
    Path("results/load_test_baseline.json"),
]
DEFAULT_JSON = Path("results/metrics_summary.json")
DEFAULT_CSV = Path("results/metrics_summary.csv")
DEFAULT_MARKDOWN = Path("results/metrics_summary.md")
DEFAULT_BASELINE_CHART = Path("results/metrics_baseline_latency.png")
DEFAULT_DASHBOARD = Path("results/metrics_dashboard.png")
CSV_FIELDS = [
    "source_file",
    "benchmark_type",
    "variant",
    "index",
    "scenario_id",
    "query_id",
    "method",
    "client_count",
    "result_count",
    "successful_request_count",
    "error_count",
    "error_rate",
    "average_latency_ms",
    "p95_latency_ms",
    "user_facing_throughput_per_second",
    "internal_elasticsearch_request_throughput_per_second",
    "document_count",
    "index_store_size_bytes",
    "docker_cpu_average_percent",
    "docker_cpu_maximum_percent",
    "docker_memory_average_percent",
    "docker_memory_maximum_percent",
    "jvm_heap_average_bytes",
    "jvm_heap_maximum_bytes",
]


class MetricsError(RuntimeError):
    """Raised when source metrics are missing, inconsistent, or malformed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MetricsError(f"cannot read benchmark report {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MetricsError(f"invalid benchmark JSON in {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise MetricsError(f"benchmark report {path} is not a JSON object")
    if report.get("status") != "passed":
        raise MetricsError(f"benchmark report {path} did not pass")
    benchmark_type = report.get("benchmark_type")
    if not isinstance(benchmark_type, str) or not benchmark_type:
        raise MetricsError(f"benchmark report {path} has no benchmark_type")
    if not isinstance(report.get("scenarios"), list) or not report["scenarios"]:
        raise MetricsError(f"benchmark report {path} has no scenarios")
    if not isinstance(report.get("runtime", {}).get("index"), dict):
        raise MetricsError(f"benchmark report {path} has no index runtime metadata")
    hydrate_external_measurements(report, path)
    return report


def hydrate_external_measurements(report: dict[str, Any], report_path: Path) -> None:
    missing = [scenario for scenario in report.get("scenarios", []) if "measurements" not in scenario]
    if not missing:
        return
    artifacts = {scenario.get("measurements_artifact", {}).get("path") for scenario in missing}
    if None in artifacts or len(artifacts) != 1:
        raise MetricsError(f"report {report_path} has invalid external measurement references")
    configured_path = Path(next(iter(artifacts)))
    measurement_path = configured_path
    if not measurement_path.is_file():
        measurement_path = report_path.parent / configured_path.name
    if not measurement_path.is_file():
        raise MetricsError(f"external measurements not found: {configured_path}")
    expected_hashes = {
        scenario["measurements_artifact"].get("sha256") for scenario in missing
    }
    actual_hash = benchmark.sha256_file(measurement_path)
    if expected_hashes != {actual_hash}:
        raise MetricsError("external measurement artifact hash mismatch")
    by_scenario: dict[str, list[dict[str, Any]]] = {
        scenario["scenario_id"]: [] for scenario in missing
    }
    try:
        with gzip.open(measurement_path, "rt", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                record = json.loads(line)
                scenario_id = record.pop("scenario_id", None)
                if scenario_id not in by_scenario:
                    raise MetricsError(
                        f"unexpected scenario {scenario_id!r} in measurements line {line_number}"
                    )
                by_scenario[scenario_id].append(record)
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricsError(f"cannot read external measurements {measurement_path}: {exc}") from exc
    for scenario in missing:
        records = by_scenario[scenario["scenario_id"]]
        expected_count = scenario["measurements_artifact"].get("record_count")
        if len(records) != expected_count:
            raise MetricsError(
                f"external measurement count mismatch for {scenario['scenario_id']}"
            )
        scenario["measurements"] = records


def _number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise MetricsError(f"{label} must be a finite number")
    return float(value)


def _optional_metric(
    metrics: dict[str, Any], metric: str, statistic: str
) -> float | None:
    value = metrics.get(metric, {}).get(statistic)
    if value is None:
        return None
    return _number(value, f"{metric}.{statistic}")


def _assert_close(reported: Any, calculated: float, label: str) -> None:
    if isinstance(reported, str):
        try:
            reported = float(reported)
        except ValueError as exc:
            raise MetricsError(f"{label} must be numeric") from exc
    reported_number = _number(reported, label)
    if not math.isclose(reported_number, calculated, rel_tol=0.01, abs_tol=0.01):
        raise MetricsError(
            f"{label} mismatch: report={reported_number}, recalculated={calculated}"
        )


def _variant(report: dict[str, Any]) -> str:
    if report.get("execution"):
        return str(report["execution"])
    index = str(report.get("index", ""))
    if "optimized" in index:
        return "optimized"
    if "load" in str(report.get("benchmark_type", "")).lower():
        return "load_test"
    return "baseline"


def normalize_scenario(
    report: dict[str, Any], scenario: dict[str, Any], source: Path
) -> dict[str, Any]:
    measurements = scenario.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise MetricsError(f"scenario in {source} has no raw measurements")
    declared_count = scenario.get("measured_request_count")
    if declared_count != len(measurements):
        raise MetricsError(
            f"scenario {scenario.get('query_id')} measurement count mismatch in {source}"
        )
    successful = [measurement for measurement in measurements if measurement.get("success") is True]
    failed = [measurement for measurement in measurements if measurement.get("success") is not True]
    latencies = [
        _number(measurement.get("client_latency_ms"), "client_latency_ms")
        for measurement in successful
    ]
    if not latencies:
        raise MetricsError(f"scenario {scenario.get('query_id')} has no successful latency")
    average_latency = statistics.fmean(latencies)
    p95_latency = benchmark.percentile_nearest_rank(latencies, 95)
    if p95_latency is None:
        raise MetricsError("cannot calculate P95 from an empty successful set")
    duration = _number(
        scenario.get("measurement_duration_seconds"), "measurement_duration_seconds"
    )
    if duration <= 0:
        raise MetricsError("measurement duration must be positive")
    success_count = len(successful)
    error_count = len(failed)
    error_rate = error_count / len(measurements)
    user_throughput = success_count / duration
    internal_count = sum(
        int(measurement.get("internal_request_count", 1)) for measurement in successful
    )
    internal_throughput = internal_count / duration

    _assert_close(scenario.get("average_latency_ms"), average_latency, "average_latency_ms")
    _assert_close(scenario.get("p95_latency_ms"), p95_latency, "p95_latency_ms")
    reported_user_throughput = scenario.get(
        "user_facing_throughput_per_second",
        scenario.get("throughput_requests_per_second"),
    )
    _assert_close(reported_user_throughput, user_throughput, "user throughput")
    reported_internal = scenario.get(
        "internal_elasticsearch_request_throughput_per_second",
        reported_user_throughput,
    )
    _assert_close(reported_internal, internal_throughput, "internal throughput")
    _assert_close(scenario.get("error_rate"), error_rate, "error_rate")
    if scenario.get("successful_request_count") != success_count:
        raise MetricsError("successful request count does not match raw measurements")
    if scenario.get("error_count") != error_count:
        raise MetricsError("error count does not match raw measurements")

    method = scenario.get("method") or scenario.get("type") or scenario.get("search_type")
    if not isinstance(method, str) or not method:
        raise MetricsError("scenario has no method/type/search_type")
    query_id = scenario.get("query_id") or scenario.get("scenario_id")
    if not isinstance(query_id, str) or not query_id:
        raise MetricsError("scenario has no query_id/scenario_id")
    client_count = scenario.get("client_count", report.get("protocol", {}).get("client_count", 1))
    if not isinstance(client_count, int) or client_count <= 0:
        raise MetricsError("client count must be a positive integer")
    result_count = scenario.get("result_count")
    if result_count is not None and (not isinstance(result_count, int) or result_count < 0):
        raise MetricsError("result count must be a non-negative integer or null")

    index_metadata = report["runtime"]["index"]
    resources = scenario.get("system_metrics") or report.get("system_metrics")
    if not isinstance(resources, dict):
        raise MetricsError("scenario/report has no system metrics")
    scenario_id = f"{query_id}:{method}:clients={client_count}"
    return {
        "source_file": str(source),
        "benchmark_type": report["benchmark_type"],
        "variant": _variant(report),
        "index": report.get("index") or index_metadata.get("name"),
        "scenario_id": scenario_id,
        "query_id": query_id,
        "method": method,
        "client_count": client_count,
        "result_count": result_count,
        "successful_request_count": success_count,
        "error_count": error_count,
        "error_rate": round(error_rate, 9),
        "average_latency_ms": round(average_latency, 6),
        "p95_latency_ms": round(p95_latency, 6),
        "user_facing_throughput_per_second": round(user_throughput, 6),
        "internal_elasticsearch_request_throughput_per_second": round(
            internal_throughput, 6
        ),
        "document_count": int(index_metadata["document_count"]),
        "index_store_size_bytes": int(index_metadata["store_size_bytes"]),
        "docker_cpu_average_percent": _optional_metric(
            resources, "docker_cpu_percent", "average"
        ),
        "docker_cpu_maximum_percent": _optional_metric(
            resources, "docker_cpu_percent", "maximum"
        ),
        "docker_memory_average_percent": _optional_metric(
            resources, "docker_memory_percent", "average"
        ),
        "docker_memory_maximum_percent": _optional_metric(
            resources, "docker_memory_percent", "maximum"
        ),
        "jvm_heap_average_bytes": _optional_metric(
            resources, "elasticsearch_jvm_heap_used_bytes", "average"
        ),
        "jvm_heap_maximum_bytes": _optional_metric(
            resources, "elasticsearch_jvm_heap_used_bytes", "maximum"
        ),
    }


def normalize_report(report: dict[str, Any], source: Path) -> list[dict[str, Any]]:
    rows = [normalize_scenario(report, scenario, source) for scenario in report["scenarios"]]
    identifiers = [row["scenario_id"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise MetricsError(f"duplicate normalized scenario identifiers in {source}")
    return rows


def source_csv_path(report: dict[str, Any], json_path: Path) -> Path:
    if report["benchmark_type"] in {"baseline_single_client_search", "diagnostic_search"}:
        return json_path.with_name("search_baseline.csv")
    if report["benchmark_type"] in {"stage6_hybrid_comparison", "stage6_diagnostic"}:
        return json_path.with_name("hybrid_performance.csv")
    return json_path.with_suffix(".csv")


def validate_source_csv(
    report: dict[str, Any], normalized: list[dict[str, Any]], csv_path: Path
) -> dict[str, Any]:
    try:
        file = csv_path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise MetricsError(f"cannot read source CSV {csv_path}: {exc}") from exc
    with file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise MetricsError(f"source CSV {csv_path} has no header")
        csv_rows = list(reader)
    if len(csv_rows) != len(normalized):
        raise MetricsError(f"source CSV {csv_path} row count does not match JSON")
    by_key = {(row["query_id"], row["method"]): row for row in normalized}
    seen: set[tuple[str, str]] = set()
    for csv_row in csv_rows:
        method = csv_row.get("method") or csv_row.get("type") or csv_row.get("search_type")
        query_id = csv_row.get("query_id") or csv_row.get("scenario_id") or ""
        key = (query_id, method or "")
        if key in seen or key not in by_key:
            raise MetricsError(f"source CSV {csv_path} has duplicate/unexpected row {key}")
        seen.add(key)
        expected = by_key[key]
        comparisons = {
            "result_count": expected["result_count"],
            "average_latency_ms": expected["average_latency_ms"],
            "p95_latency_ms": expected["p95_latency_ms"],
            "error_rate": expected["error_rate"],
        }
        for field, value in comparisons.items():
            raw = csv_row.get(field)
            if value is None:
                if raw not in (None, ""):
                    raise MetricsError(f"source CSV {csv_path} unexpected {field}")
            else:
                _assert_close(raw, float(value), f"{csv_path}:{key}:{field}")
        throughput_field = (
            "user_facing_throughput_per_second"
            if "user_facing_throughput_per_second" in csv_row
            else "throughput_requests_per_second"
        )
        _assert_close(
            csv_row.get(throughput_field),
            expected["user_facing_throughput_per_second"],
            f"{csv_path}:{key}:throughput",
        )
    return {
        "path": str(csv_path),
        "sha256": benchmark.sha256_file(csv_path),
        "row_count": len(csv_rows),
        "status": "passed",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Metrics Summary",
        "",
        "All latency, P95, throughput, and error-rate values below were recalculated from raw request measurements.",
        "",
        "| Source | Scenario | Avg ms | P95 ms | User QPS | Internal QPS | Error rate | Docs | Index MiB | CPU avg/max | Memory avg/max |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        cpu = f"{row['docker_cpu_average_percent']:.2f}/{row['docker_cpu_maximum_percent']:.2f}"
        memory = (
            f"{row['docker_memory_average_percent']:.2f}/{row['docker_memory_maximum_percent']:.2f}"
        )
        lines.append(
            "| {source} | {scenario} | {avg:.3f} | {p95:.3f} | {user:.2f} | "
            "{internal:.2f} | {error:.3%} | {docs:,} | {size:.2f} | {cpu} | {memory} |".format(
                source=Path(row["source_file"]).stem,
                scenario=row["scenario_id"],
                avg=row["average_latency_ms"],
                p95=row["p95_latency_ms"],
                user=row["user_facing_throughput_per_second"],
                internal=row["internal_elasticsearch_request_throughput_per_second"],
                error=row["error_rate"],
                docs=row["document_count"],
                size=row["index_store_size_bytes"] / (1024 * 1024),
                cpu=cpu,
                memory=memory,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_pyplot() -> Any:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise MetricsError("matplotlib is required to generate stage-7 charts") from exc
    return plt


def write_baseline_latency_chart(path: Path, rows: list[dict[str, Any]]) -> None:
    baseline = [
        row for row in rows if row["benchmark_type"] == "baseline_single_client_search"
    ]
    if not baseline:
        raise MetricsError("no stage-5 baseline rows are available for the sample chart")
    plt = _load_pyplot()
    path.parent.mkdir(parents=True, exist_ok=True)
    labels = [row["query_id"] for row in baseline]
    positions = list(range(len(baseline)))
    width = 0.38
    figure, axis = plt.subplots(figsize=(14, 7))
    axis.bar(
        [position - width / 2 for position in positions],
        [row["average_latency_ms"] for row in baseline],
        width,
        label="Average",
    )
    axis.bar(
        [position + width / 2 for position in positions],
        [row["p95_latency_ms"] for row in baseline],
        width,
        label="P95",
    )
    axis.set_title("Stage 7 validation: stage-5 baseline latency")
    axis.set_ylabel("End-to-end client latency (ms)")
    axis.set_xticks(positions, labels, rotation=35, ha="right")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    figure.tight_layout()
    temporary = path.with_name(path.stem + ".tmp" + path.suffix)
    figure.savefig(temporary, dpi=160)
    plt.close(figure)
    temporary.replace(path)


def write_dashboard(path: Path, rows: list[dict[str, Any]]) -> None:
    plt = _load_pyplot()
    path.parent.mkdir(parents=True, exist_ok=True)
    labels = [f"{Path(row['source_file']).stem}: {row['query_id']}:{row['method']}" for row in rows]
    positions = list(range(len(rows)))
    figure, axes = plt.subplots(1, 3, figsize=(22, max(9, len(rows) * 0.48)))
    axes[0].barh(
        [position - 0.2 for position in positions],
        [row["average_latency_ms"] for row in rows],
        0.4,
        label="Average",
    )
    axes[0].barh(
        [position + 0.2 for position in positions],
        [row["p95_latency_ms"] for row in rows],
        0.4,
        label="P95",
    )
    axes[0].set_title("Latency (ms)")
    axes[0].legend()
    axes[1].barh(
        positions,
        [row["user_facing_throughput_per_second"] for row in rows],
        0.7,
        label="User-facing",
    )
    axes[1].set_title("Throughput (searches/s)")
    axes[2].barh(
        [position - 0.2 for position in positions],
        [row["docker_cpu_average_percent"] for row in rows],
        0.4,
        label="CPU avg %",
    )
    axes[2].barh(
        [position + 0.2 for position in positions],
        [row["docker_memory_average_percent"] for row in rows],
        0.4,
        label="Memory avg %",
    )
    axes[2].set_title("Docker resources")
    axes[2].legend()
    for axis in axes:
        axis.set_yticks(positions, labels if axis is axes[0] else [])
        axis.grid(axis="x", alpha=0.25)
        axis.invert_yaxis()
    figure.suptitle("Stage 7 normalized benchmark metrics", fontsize=16)
    figure.tight_layout()
    temporary = path.with_name(path.stem + ".tmp" + path.suffix)
    figure.savefig(temporary, dpi=150)
    plt.close(figure)
    temporary.replace(path)


def build_summary(input_paths: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    for path in input_paths:
        report = load_report(path)
        normalized = normalize_report(report, path)
        csv_validation = validate_source_csv(report, normalized, source_csv_path(report, path))
        rows.extend(normalized)
        inputs.append(
            {
                "json_path": str(path),
                "json_sha256": benchmark.sha256_file(path),
                "benchmark_type": report["benchmark_type"],
                "scenario_count": len(normalized),
                "csv_validation": csv_validation,
            }
        )
    identifiers = [(row["source_file"], row["scenario_id"]) for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise MetricsError("duplicate source/scenario pair across report inputs")
    summary = {
        "status": "passed",
        "schema_version": 1,
        "generated_at": utc_now(),
        "report_type": "stage7_normalized_metrics",
        "definitions": {
            "latency": "end-to-end client latency for each successful measured request",
            "average_latency": "arithmetic mean of successful end-to-end latencies",
            "p95_latency": "nearest-rank P95 over successful end-to-end latencies",
            "user_facing_throughput": "successful user requests divided by measured scenario wall time",
            "internal_throughput": "successful internal Elasticsearch requests divided by measured scenario wall time",
            "error_rate": "failed measured user requests divided by all measured user requests",
            "warmups_included": False,
        },
        "input_count": len(inputs),
        "scenario_count": len(rows),
        "validated_metric_count": 10,
        "inputs": inputs,
        "rows": rows,
    }
    return summary, rows


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", dest="inputs")
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv-output", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--baseline-chart", type=Path, default=DEFAULT_BASELINE_CHART)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    inputs = args.inputs or DEFAULT_INPUTS
    try:
        summary, rows = build_summary(inputs)
        benchmark.atomic_write_json(args.json_output, summary)
        write_csv(args.csv_output, rows)
        write_markdown(args.markdown_output, rows)
        write_baseline_latency_chart(args.baseline_chart, rows)
        write_dashboard(args.dashboard, rows)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "input_count": summary["input_count"],
                    "scenario_count": summary["scenario_count"],
                    "json_output": str(args.json_output),
                    "csv_output": str(args.csv_output),
                    "markdown_output": str(args.markdown_output),
                    "baseline_chart": str(args.baseline_chart),
                    "dashboard": str(args.dashboard),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (MetricsError, benchmark.BenchmarkError, OSError) as exc:
        print(f"metrics error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
