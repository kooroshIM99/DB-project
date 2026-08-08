#!/usr/bin/env python3
"""Run and persist the reproducible stage-5 single-client search baseline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import search_queries
except ModuleNotFoundError:  # Direct execution from scripts/.
    import search_queries  # type: ignore[no-redef]


DEFAULT_QUERY_FILE = Path("queries/search_queries.json")
DEFAULT_MAPPING_FILE = Path("elasticsearch/mappings/arxiv_papers_baseline.json")
DEFAULT_DATASET = Path("dataset/arxiv_project_sample_50k_cleaned.jsonl")
DEFAULT_INGESTION_REPORT = Path("results/ingestion_baseline.json")
DEFAULT_JSON_REPORT = Path("results/search_baseline.json")
DEFAULT_CSV_REPORT = Path("results/search_baseline.csv")
DEFAULT_CHART = Path("results/search_baseline_latency.png")
BASELINE_INDEX = "arxiv_papers_baseline"
BASELINE_WARMUPS = 5
BASELINE_ITERATIONS = 30
BASELINE_SEED = 20250808
RESOURCE_INTERVAL_SECONDS = 1.0
CONTAINER_NAME = "db-project-elasticsearch"


class BenchmarkError(RuntimeError):
    """Raised when a valid baseline cannot be produced."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BenchmarkError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize_numbers(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"sample_count": 0, "average": None, "maximum": None}
    return {
        "sample_count": len(values),
        "average": round(statistics.fmean(values), 6),
        "maximum": round(max(values), 6),
    }


def _nested_number(value: Any, *keys: str) -> float | None:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _run_command(command: list[str], timeout: float = 6.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc)}
    if completed.returncode != 0:
        return {
            "available": False,
            "error": completed.stderr.strip() or f"exit status {completed.returncode}",
        }
    return {"available": True, "output": completed.stdout.strip()}


def inspect_runtime(base_url: str, index: str, timeout: float) -> dict[str, Any]:
    root = search_queries.request_json(base_url, "GET", "/", timeout=timeout)
    settings = search_queries.request_json(
        base_url, "GET", f"/{index}/_settings", timeout=timeout
    )
    stats = search_queries.request_json(
        base_url, "GET", f"/{index}/_stats/store,docs", timeout=timeout
    )
    nodes = search_queries.request_json(
        base_url,
        "GET",
        "/_nodes/_local/jvm,os,process?filter_path=nodes.*.jvm.mem.heap_max_in_bytes,"
        "nodes.*.jvm.input_arguments,nodes.*.os.available_processors,"
        "nodes.*.process.max_file_descriptors",
        timeout=timeout,
    )
    try:
        index_settings = settings[index]["settings"]["index"]
        index_stats = stats["indices"][index]
    except (KeyError, TypeError) as exc:
        raise BenchmarkError(f"unexpected index metadata response: {exc}") from exc

    docker_version = _run_command(["docker", "version", "--format", "{{.Server.Version}}"])
    compose_version = _run_command(["docker", "compose", "version", "--short"])
    inspect = _run_command(["docker", "inspect", CONTAINER_NAME])
    container: dict[str, Any] = {"name": CONTAINER_NAME, "available": False}
    if inspect.get("available"):
        try:
            data = json.loads(inspect["output"])[0]
            host_config = data["HostConfig"]
            container = {
                "name": CONTAINER_NAME,
                "available": True,
                "image": data["Config"]["Image"],
                "memory_limit_bytes": host_config.get("Memory"),
                "nano_cpus": host_config.get("NanoCpus"),
                "cpu_limit": (
                    host_config.get("NanoCpus", 0) / 1_000_000_000
                    if host_config.get("NanoCpus")
                    else None
                ),
                "status": data["State"].get("Status"),
            }
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            container = {"name": CONTAINER_NAME, "available": False, "error": str(exc)}
    else:
        container["error"] = inspect.get("error")

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "elasticsearch_version": root.get("version", {}).get("number"),
        "cluster_name": root.get("cluster_name"),
        "docker_version": docker_version.get("output") if docker_version.get("available") else None,
        "docker_error": docker_version.get("error"),
        "docker_compose_version": (
            compose_version.get("output") if compose_version.get("available") else None
        ),
        "docker_compose_error": compose_version.get("error"),
        "container": container,
        "index": {
            "name": index,
            "uuid": index_settings.get("uuid"),
            "number_of_shards": int(index_settings["number_of_shards"]),
            "number_of_replicas": int(index_settings["number_of_replicas"]),
            "document_count": int(index_stats["total"]["docs"]["count"]),
            "store_size_bytes": int(index_stats["total"]["store"]["size_in_bytes"]),
            "primary_store_size_bytes": int(
                index_stats["primaries"]["store"]["size_in_bytes"]
            ),
        },
        "elasticsearch_node_configuration": nodes,
    }


def collect_resource_sample(base_url: str, timeout: float) -> dict[str, Any]:
    sampled_at = utc_now()
    monotonic = time.perf_counter()
    sample: dict[str, Any] = {
        "sampled_at": sampled_at,
        "monotonic_seconds": monotonic,
    }
    try:
        nodes = search_queries.request_json(
            base_url,
            "GET",
            "/_nodes/_local/stats/jvm,process,os?filter_path=nodes.*.jvm.mem.heap_used_in_bytes,"
            "nodes.*.jvm.mem.heap_used_percent,nodes.*.process.cpu.percent,"
            "nodes.*.process.mem.total_virtual_in_bytes,nodes.*.os.cpu.percent",
            timeout=timeout,
        )
        node = next(iter(nodes.get("nodes", {}).values()))
        sample["elasticsearch"] = {
            "jvm_heap_used_bytes": _nested_number(node, "jvm", "mem", "heap_used_in_bytes"),
            "jvm_heap_used_percent": _nested_number(node, "jvm", "mem", "heap_used_percent"),
            "process_cpu_percent": _nested_number(node, "process", "cpu", "percent"),
            "os_cpu_percent": _nested_number(node, "os", "cpu", "percent"),
            "process_virtual_memory_bytes": _nested_number(
                node, "process", "mem", "total_virtual_in_bytes"
            ),
        }
    except (search_queries.SearchError, StopIteration, TypeError) as exc:
        sample["elasticsearch_error"] = str(exc)

    docker = _run_command(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            CONTAINER_NAME,
        ],
        timeout=max(3.0, timeout),
    )
    if docker.get("available"):
        try:
            data = json.loads(docker["output"])
            sample["docker"] = {
                "cpu_percent": float(str(data["CPUPerc"]).rstrip("%")),
                "memory_percent": float(str(data["MemPerc"]).rstrip("%")),
                "memory_usage": data.get("MemUsage"),
                "block_io": data.get("BlockIO"),
                "network_io": data.get("NetIO"),
                "pids": int(data["PIDs"]) if data.get("PIDs") else None,
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            sample["docker_error"] = f"cannot parse docker stats: {exc}"
    else:
        sample["docker_error"] = docker.get("error")
    return sample


class ResourceSampler:
    def __init__(self, base_url: str, interval: float, timeout: float) -> None:
        self.base_url = base_url
        self.interval = interval
        self.timeout = timeout
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="resource-sampler", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            self.samples.append(collect_resource_sample(self.base_url, self.timeout))
            elapsed = time.perf_counter() - started
            self._stop.wait(max(0.0, self.interval - elapsed))

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(8.0, self.timeout + 2.0))


def summarize_resources(samples: list[dict[str, Any]]) -> dict[str, Any]:
    fields: list[tuple[str, str, str]] = [
        ("docker", "cpu_percent", "docker_cpu_percent"),
        ("docker", "memory_percent", "docker_memory_percent"),
        ("elasticsearch", "jvm_heap_used_bytes", "elasticsearch_jvm_heap_used_bytes"),
        ("elasticsearch", "jvm_heap_used_percent", "elasticsearch_jvm_heap_used_percent"),
        ("elasticsearch", "process_cpu_percent", "elasticsearch_process_cpu_percent"),
        ("elasticsearch", "os_cpu_percent", "host_os_cpu_percent"),
    ]
    summary: dict[str, Any] = {"resource_sample_count": len(samples)}
    for section, field, label in fields:
        values = [
            float(sample[section][field])
            for sample in samples
            if isinstance(sample.get(section), dict)
            and isinstance(sample[section].get(field), (int, float))
        ]
        summary[label] = summarize_numbers(values)
    summary["sampling_errors"] = [
        {
            "sampled_at": sample["sampled_at"],
            "elasticsearch_error": sample.get("elasticsearch_error"),
            "docker_error": sample.get("docker_error"),
        }
        for sample in samples
        if sample.get("elasticsearch_error") or sample.get("docker_error")
    ]
    return summary


def execute_scenario(
    *,
    base_url: str,
    index: str,
    query: dict[str, Any],
    execution: str,
    warmups: int,
    iterations: int,
    timeout: float,
) -> dict[str, Any]:
    warmup_errors: list[str] = []
    for _ in range(warmups):
        try:
            search_queries.execute_query(
                base_url=base_url,
                index=index,
                query=query,
                execution=execution,
                timeout=timeout,
            )
        except search_queries.SearchError as exc:
            warmup_errors.append(str(exc))

    started_at = utc_now()
    started_monotonic = time.perf_counter()
    measurements: list[dict[str, Any]] = []
    successful_latencies: list[float] = []
    failed_latencies: list[float] = []
    result_counts: list[int] = []
    aggregation_sample: dict[str, Any] | None = None
    for request_number in range(1, iterations + 1):
        request_started = time.perf_counter_ns()
        try:
            response, latency_ms = search_queries.execute_query(
                base_url=base_url,
                index=index,
                query=query,
                execution=execution,
                timeout=timeout,
            )
            result_count = search_queries.total_hits(response)
            successful_latencies.append(latency_ms)
            result_counts.append(result_count)
            if aggregation_sample is None and "aggregations" in response:
                aggregation_sample = response["aggregations"]
            measurements.append(
                {
                    "request_number": request_number,
                    "success": True,
                    "client_latency_ms": round(latency_ms, 6),
                    "elasticsearch_took_ms": response.get("took"),
                    "result_count": result_count,
                }
            )
        except search_queries.SearchError as exc:
            latency_ms = (time.perf_counter_ns() - request_started) / 1_000_000
            failed_latencies.append(latency_ms)
            measurements.append(
                {
                    "request_number": request_number,
                    "success": False,
                    "client_latency_ms": round(latency_ms, 6),
                    "error": str(exc),
                }
            )
    duration = time.perf_counter() - started_monotonic
    success_count = len(successful_latencies)
    error_count = len(failed_latencies)
    scenario: dict[str, Any] = {
        "query_id": query["id"],
        "type": query["type"],
        "intent": query["intent"],
        "user_input": query["user_input"],
        "logical_contract_sha256": canonical_hash(
            {
                key: query[key]
                for key in ("id", "type", "intent", "user_input")
            }
        ),
        "execution_contract_sha256": canonical_hash(query["execution"][execution]),
        "execution_contract": query["execution"][execution],
        "started_at": started_at,
        "finished_at": utc_now(),
        "started_monotonic_seconds": started_monotonic,
        "finished_monotonic_seconds": time.perf_counter(),
        "warmup_request_count": warmups,
        "warmup_error_count": len(warmup_errors),
        "warmup_errors": warmup_errors,
        "measured_request_count": iterations,
        "successful_request_count": success_count,
        "error_count": error_count,
        "error_rate": round(error_count / iterations, 9),
        "measurement_duration_seconds": round(duration, 6),
        "throughput_requests_per_second": round(success_count / duration, 6) if duration else None,
        "average_latency_ms": (
            round(statistics.fmean(successful_latencies), 6) if successful_latencies else None
        ),
        "p95_latency_ms": (
            round(percentile_nearest_rank(successful_latencies, 95) or 0, 6)
            if successful_latencies
            else None
        ),
        "minimum_latency_ms": round(min(successful_latencies), 6) if successful_latencies else None,
        "maximum_latency_ms": round(max(successful_latencies), 6) if successful_latencies else None,
        "average_failed_latency_ms": (
            round(statistics.fmean(failed_latencies), 6) if failed_latencies else None
        ),
        "result_count": result_counts[0] if result_counts else None,
        "result_count_consistent": len(set(result_counts)) <= 1,
        "measurements": measurements,
    }
    if aggregation_sample is not None:
        scenario["aggregation_sample"] = aggregation_sample
    return scenario


def attach_scenario_resources(
    scenarios: list[dict[str, Any]], samples: list[dict[str, Any]]
) -> None:
    for scenario in scenarios:
        selected = [
            sample
            for sample in samples
            if scenario["started_monotonic_seconds"]
            <= sample["monotonic_seconds"]
            <= scenario["finished_monotonic_seconds"]
        ]
        if not selected and samples:
            midpoint = (
                scenario["started_monotonic_seconds"]
                + scenario["finished_monotonic_seconds"]
            ) / 2
            selected = [min(samples, key=lambda sample: abs(sample["monotonic_seconds"] - midpoint))]
        scenario["system_metrics"] = summarize_resources(selected)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_summary_csv(path: Path, scenarios: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fields = [
        "query_id",
        "type",
        "result_count",
        "successful_request_count",
        "error_count",
        "error_rate",
        "average_latency_ms",
        "p95_latency_ms",
        "throughput_requests_per_second",
        "measurement_duration_seconds",
        "docker_cpu_average_percent",
        "docker_cpu_maximum_percent",
        "docker_memory_average_percent",
        "docker_memory_maximum_percent",
        "jvm_heap_average_bytes",
        "jvm_heap_maximum_bytes",
    ]
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for scenario in scenarios:
            metrics = scenario["system_metrics"]
            writer.writerow(
                {
                    **{field: scenario.get(field) for field in fields},
                    "docker_cpu_average_percent": metrics["docker_cpu_percent"]["average"],
                    "docker_cpu_maximum_percent": metrics["docker_cpu_percent"]["maximum"],
                    "docker_memory_average_percent": metrics["docker_memory_percent"]["average"],
                    "docker_memory_maximum_percent": metrics["docker_memory_percent"]["maximum"],
                    "jvm_heap_average_bytes": metrics["elasticsearch_jvm_heap_used_bytes"]["average"],
                    "jvm_heap_maximum_bytes": metrics["elasticsearch_jvm_heap_used_bytes"]["maximum"],
                }
            )
    temporary.replace(path)


def write_latency_chart(path: Path, scenarios: list[dict[str, Any]]) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise BenchmarkError("matplotlib is required to create the stage-5 chart") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    labels = [scenario["query_id"] for scenario in scenarios]
    averages = [scenario["average_latency_ms"] or 0 for scenario in scenarios]
    p95s = [scenario["p95_latency_ms"] or 0 for scenario in scenarios]
    positions = list(range(len(scenarios)))
    width = 0.38
    figure, axis = plt.subplots(figsize=(13, 7))
    axis.bar([position - width / 2 for position in positions], averages, width, label="Average")
    axis.bar([position + width / 2 for position in positions], p95s, width, label="P95")
    axis.set_ylabel("End-to-end client latency (ms)")
    axis.set_title("Stage 5: Elasticsearch baseline search latency")
    axis.set_xticks(positions, labels, rotation=35, ha="right")
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    temporary = path.with_name(path.stem + ".tmp" + path.suffix)
    figure.savefig(temporary, dpi=160)
    plt.close(figure)
    temporary.replace(path)


def run_benchmark(
    *,
    base_url: str,
    query_file: Path,
    mapping_file: Path,
    dataset: Path,
    ingestion_report: Path,
    execution: str,
    index: str,
    warmups: int,
    iterations: int,
    seed: int,
    timeout: float,
    resource_interval: float,
    allow_nonstandard: bool,
) -> dict[str, Any]:
    if warmups < 0 or iterations <= 0 or timeout <= 0 or resource_interval <= 0:
        raise BenchmarkError("warmups must be non-negative and other numeric values positive")
    compliant = (
        index == BASELINE_INDEX
        and execution == "baseline"
        and warmups >= BASELINE_WARMUPS
        and iterations >= BASELINE_ITERATIONS
    )
    if not compliant and not allow_nonstandard:
        raise BenchmarkError(
            "non-standard parameters cannot produce a baseline; use the stage-5 defaults or "
            "pass --allow-nonstandard for a diagnostic run"
        )
    contract = search_queries.load_query_contract(query_file, execution)
    try:
        ingestion = json.loads(ingestion_report.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BenchmarkError(f"cannot read ingestion report {ingestion_report}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"invalid ingestion report JSON in {ingestion_report}: {exc}") from exc
    if (
        not isinstance(ingestion, dict)
        or ingestion.get("status") != "passed"
        or ingestion.get("index") != index
        or not isinstance(ingestion.get("batch_size"), int)
    ):
        raise BenchmarkError("ingestion report does not describe a passed run for the target index")
    queries = list(contract["queries"])
    random.Random(seed).shuffle(queries)
    started_at = utc_now()
    benchmark_started = time.perf_counter()
    runtime = inspect_runtime(base_url, index, timeout)
    if runtime["index"]["document_count"] != 50_000:
        raise BenchmarkError(
            f"baseline index must contain 50,000 documents, found "
            f"{runtime['index']['document_count']}"
        )

    sampler = ResourceSampler(base_url, resource_interval, min(timeout, 10.0))
    sampler.start()
    scenarios: list[dict[str, Any]] = []
    try:
        for query in queries:
            scenarios.append(
                execute_scenario(
                    base_url=base_url,
                    index=index,
                    query=query,
                    execution=execution,
                    warmups=warmups,
                    iterations=iterations,
                    timeout=timeout,
                )
            )
    finally:
        sampler.stop()
    finished_monotonic = time.perf_counter()
    attach_scenario_resources(scenarios, sampler.samples)
    total_measured = sum(scenario["measured_request_count"] for scenario in scenarios)
    total_success = sum(scenario["successful_request_count"] for scenario in scenarios)
    total_errors = sum(scenario["error_count"] for scenario in scenarios)
    warmup_errors = sum(scenario["warmup_error_count"] for scenario in scenarios)
    result_counts_consistent = all(scenario["result_count_consistent"] for scenario in scenarios)
    resources = summarize_resources(sampler.samples)
    resource_metrics_complete = (
        resources["docker_cpu_percent"]["sample_count"] > 0
        and resources["docker_memory_percent"]["sample_count"] > 0
        and resources["elasticsearch_jvm_heap_used_bytes"]["sample_count"] > 0
    )
    status = (
        "passed"
        if total_errors == 0
        and warmup_errors == 0
        and result_counts_consistent
        and resource_metrics_complete
        else "failed"
    )
    return {
        "status": status,
        "benchmark_type": "baseline_single_client_search" if compliant else "diagnostic_search",
        "is_performance_baseline": compliant,
        "protocol_compliant": compliant,
        "host": base_url,
        "index": index,
        "execution": execution,
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds_including_metadata_and_sampling": round(
            finished_monotonic - benchmark_started, 6
        ),
        "protocol": {
            "client_count": 1,
            "warmups_per_query": warmups,
            "measured_iterations_per_query": iterations,
            "latency_definition": "client end-to-end request/complete-response wall time",
            "p95_definition": "nearest-rank percentile over successful measured requests",
            "throughput_definition": "successful requests / measured scenario wall time",
            "warmups_excluded_from_metrics": True,
            "track_total_hits": True,
            "query_scenario_order_seed": seed,
            "query_scenario_order": [query["id"] for query in queries],
            "cache_cleared": False,
            "request_timeout_seconds": timeout,
            "resource_sample_interval_seconds": resource_interval,
            "resource_sources": ["docker stats", "Elasticsearch nodes stats API"],
            "ingestion_batch_size": ingestion["batch_size"],
        },
        "artifacts": {
            "query_contract_path": str(query_file),
            "query_contract_sha256": sha256_file(query_file),
            "logical_query_contract_sha256": canonical_hash(
                [
                    {key: query[key] for key in ("id", "type", "intent", "user_input")}
                    for query in contract["queries"]
                ]
            ),
            "execution_query_contract_sha256": canonical_hash(
                [query["execution"][execution] for query in contract["queries"]]
            ),
            "mapping_path": str(mapping_file),
            "mapping_sha256": sha256_file(mapping_file),
            "dataset_path": str(dataset),
            "dataset_sha256": sha256_file(dataset),
            "dataset_size_bytes": dataset.stat().st_size,
            "ingestion_report_path": str(ingestion_report),
            "ingestion_report_sha256": sha256_file(ingestion_report),
        },
        "ingestion_contract": {
            "batch_size": ingestion["batch_size"],
            "dataset_sha256": ingestion.get("dataset_sha256"),
            "document_count": ingestion.get("document_count"),
            "index": ingestion.get("index"),
        },
        "runtime": runtime,
        "totals": {
            "query_scenario_count": len(scenarios),
            "warmup_request_count": warmups * len(scenarios),
            "warmup_error_count": warmup_errors,
            "measured_request_count": total_measured,
            "successful_request_count": total_success,
            "error_count": total_errors,
            "error_rate": round(total_errors / total_measured, 9),
        },
        "resource_metrics_complete": resource_metrics_complete,
        "system_metrics": resources,
        "resource_samples": sampler.samples,
        "scenarios": scenarios,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default=os.environ.get("ELASTICSEARCH_URL", search_queries.DEFAULT_ES_URL)
    )
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_FILE)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_FILE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--ingestion-report", type=Path, default=DEFAULT_INGESTION_REPORT)
    parser.add_argument("--execution", default="baseline")
    parser.add_argument("--index", default=BASELINE_INDEX)
    parser.add_argument("--warmups", type=int, default=BASELINE_WARMUPS)
    parser.add_argument("--iterations", type=int, default=BASELINE_ITERATIONS)
    parser.add_argument("--seed", type=int, default=BASELINE_SEED)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--resource-interval", type=float, default=RESOURCE_INTERVAL_SECONDS)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--csv-report", type=Path, default=DEFAULT_CSV_REPORT)
    parser.add_argument("--chart", type=Path, default=DEFAULT_CHART)
    parser.add_argument("--allow-nonstandard", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        report = run_benchmark(
            base_url=args.host,
            query_file=args.queries,
            mapping_file=args.mapping,
            dataset=args.dataset,
            ingestion_report=args.ingestion_report,
            execution=args.execution,
            index=args.index,
            warmups=args.warmups,
            iterations=args.iterations,
            seed=args.seed,
            timeout=args.timeout,
            resource_interval=args.resource_interval,
            allow_nonstandard=args.allow_nonstandard,
        )
        atomic_write_json(args.json_report, report)
        write_summary_csv(args.csv_report, report["scenarios"])
        write_latency_chart(args.chart, report["scenarios"])
        print(json.dumps({
            "status": report["status"],
            "protocol_compliant": report["protocol_compliant"],
            "json_report": str(args.json_report),
            "csv_report": str(args.csv_report),
            "chart": str(args.chart),
            "totals": report["totals"],
        }, indent=2, sort_keys=True))
        return 0 if report["status"] == "passed" else 1
    except (BenchmarkError, search_queries.SearchError, OSError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
