#!/usr/bin/env python3
"""Run the reproducible stage-8 multi-client Elasticsearch load tests."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import http.client
import json
import math
import os
import random
import socket
import statistics
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark, hybrid_search, search_queries
except ModuleNotFoundError:  # Direct execution from scripts/.
    import benchmark  # type: ignore[no-redef]
    import hybrid_search  # type: ignore[no-redef]
    import search_queries  # type: ignore[no-redef]


DEFAULT_CONTRACT = Path("queries/load_test_scenarios.json")
DEFAULT_BASE_QUERIES = Path("queries/search_queries.json")
DEFAULT_HYBRID_QUERIES = Path("queries/hybrid_comparison_queries.json")
DEFAULT_MAPPING = Path("elasticsearch/mappings/arxiv_papers_baseline.json")
DEFAULT_DATASET = Path("dataset/arxiv_project_sample_50k_cleaned.jsonl")
DEFAULT_INGESTION_REPORT = Path("results/ingestion_baseline.json")
DEFAULT_JSON = Path("results/load_test_baseline.json")
DEFAULT_MEASUREMENTS = Path("results/load_test_measurements.jsonl.gz")
DEFAULT_CSV = Path("results/load_test_baseline.csv")
DEFAULT_LATENCY_CHART = Path("results/load_test_latency.png")
DEFAULT_THROUGHPUT_CHART = Path("results/load_test_throughput.png")
BASELINE_INDEX = "arxiv_papers_baseline"
SEARCH_TYPES = ("keyword", "contain", "fuzzy", "hybrid")
MAX_RECORDED_ERRORS = 100


class LoadTestError(RuntimeError):
    """Raised when the workload contract or a load-test operation is invalid."""


def stable_seed(base_seed: int, scenario_id: str, client_id: int, phase: str) -> int:
    payload = f"{base_seed}:{scenario_id}:{client_id}:{phase}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LoadTestError(f"cannot read load-test contract {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LoadTestError(f"invalid load-test contract JSON in {path}: {exc}") from exc
    if not isinstance(contract, dict):
        raise LoadTestError("load-test contract must be an object")
    defaults = contract.get("defaults")
    pools = contract.get("query_pools")
    scenarios = contract.get("mandatory_scenarios")
    if not isinstance(defaults, dict) or not isinstance(pools, dict):
        raise LoadTestError("load-test contract requires defaults and query_pools")
    if set(pools) != set(SEARCH_TYPES):
        raise LoadTestError("load-test contract must define four query pools")
    if not isinstance(scenarios, list) or len(scenarios) != 10:
        raise LoadTestError("load-test contract must contain exactly 10 mandatory scenarios")
    expected = {
        (search_type, clients)
        for search_type in SEARCH_TYPES[:3]
        for clients in (1, 5, 10)
    } | {("hybrid", 10)}
    actual: set[tuple[str, int]] = set()
    ids: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("id"), str):
            raise LoadTestError("every load-test scenario requires an id")
        if scenario["id"] in ids:
            raise LoadTestError(f"duplicate scenario id {scenario['id']!r}")
        ids.add(scenario["id"])
        search_type = scenario.get("search_type")
        clients = scenario.get("client_count")
        if search_type not in SEARCH_TYPES or not isinstance(clients, int):
            raise LoadTestError(f"invalid scenario {scenario['id']!r}")
        actual.add((search_type, clients))
    if actual != expected:
        raise LoadTestError("mandatory scenarios do not match the required 10-scenario matrix")
    for search_type, pool in pools.items():
        if not isinstance(pool, dict) or not isinstance(pool.get("query_ids"), list):
            raise LoadTestError(f"invalid {search_type} query pool")
        weights = pool.get("weights")
        if (
            not isinstance(weights, list)
            or len(weights) != len(pool["query_ids"])
            or not weights
            or any(not isinstance(value, (int, float)) or value <= 0 for value in weights)
        ):
            raise LoadTestError(f"invalid {search_type} query weights")
    required_defaults = {
        "base_seed": int,
        "warmup_seconds": (int, float),
        "measurement_seconds": (int, float),
        "minimum_measured_requests": int,
        "request_timeout_seconds": (int, float),
        "resource_sample_interval_seconds": (int, float),
    }
    for key, expected_type in required_defaults.items():
        if not isinstance(defaults.get(key), expected_type) or defaults[key] <= 0:
            raise LoadTestError(f"invalid positive default {key!r}")
    return contract


def load_query_pools(
    contract: dict[str, Any], base_path: Path, hybrid_path: Path
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    base_execution = contract.get("defaults", {}).get("base_execution", "baseline")
    base_contract = search_queries.load_query_contract(base_path, base_execution)
    hybrid_contract = hybrid_search.load_contract(hybrid_path)
    base_by_id = {query["id"]: query for query in base_contract["queries"]}
    hybrid_by_id = {query["id"]: query for query in hybrid_contract["queries"]}
    pools: dict[str, list[dict[str, Any]]] = {}
    for search_type, definition in contract["query_pools"].items():
        source = hybrid_by_id if definition["source"] == "hybrid" else base_by_id
        queries: list[dict[str, Any]] = []
        for query_id in definition["query_ids"]:
            if query_id not in source:
                raise LoadTestError(f"query {query_id!r} not found for {search_type} pool")
            query = source[query_id]
            if search_type != "hybrid" and query.get("type") != search_type:
                raise LoadTestError(f"query {query_id!r} has wrong type for {search_type} pool")
            queries.append(query)
        pools[search_type] = queries
    return pools, hybrid_contract


class PersistentElasticsearchSession:
    """One reusable HTTP/1.1 connection owned by exactly one client path."""

    def __init__(self, base_url: str, timeout: float) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise LoadTestError("stage-8 persistent client requires an http:// host")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.prefix = parsed.path.rstrip("/")
        self.timeout = timeout
        self.connection: http.client.HTTPConnection | None = None
        self.connection_creations = 0

    def _connect(self) -> http.client.HTTPConnection:
        if self.connection is None:
            self.connection = http.client.HTTPConnection(
                self.host, self.port, timeout=self.timeout
            )
            self.connection_creations += 1
        return self.connection

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def search(self, index: str, dsl: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(dsl, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path = f"{self.prefix}/{urllib.parse.quote(index, safe='')}/_search"
        connection = self._connect()
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Connection": "keep-alive",
                },
            )
            response = connection.getresponse()
            raw = response.read()
            if not 200 <= response.status < 300:
                raise LoadTestError(
                    f"POST {path} failed with HTTP {response.status}: "
                    f"{raw.decode('utf-8', errors='replace')}"
                )
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(parsed, dict):
                raise LoadTestError("Elasticsearch returned non-object JSON")
            if parsed.get("timed_out") is True:
                raise LoadTestError("Elasticsearch search timed out")
            return parsed
        except (OSError, TimeoutError, socket.timeout, http.client.HTTPException, json.JSONDecodeError):
            self.close()
            raise


class LoadClient:
    def __init__(
        self,
        *,
        base_url: str,
        index: str,
        search_type: str,
        timeout: float,
        hybrid_contract: dict[str, Any],
        base_execution: str = "baseline",
    ) -> None:
        self.index = index
        self.search_type = search_type
        self.hybrid_contract = hybrid_contract
        self.base_execution = base_execution
        self.sessions: dict[str, PersistentElasticsearchSession] = {}
        self.executor: ThreadPoolExecutor | None = None
        if search_type == "hybrid":
            self.sessions = {
                method: PersistentElasticsearchSession(base_url, timeout)
                for method in ("keyword", "contain", "fuzzy")
            }
            self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="load-hybrid")
        else:
            self.sessions[search_type] = PersistentElasticsearchSession(base_url, timeout)

    def close(self) -> None:
        if self.executor is not None:
            self.executor.shutdown(wait=True)
        for session in self.sessions.values():
            session.close()

    @property
    def connection_creations(self) -> int:
        return sum(session.connection_creations for session in self.sessions.values())

    def execute(self, query: dict[str, Any]) -> dict[str, Any]:
        if self.search_type != "hybrid":
            response = self.sessions[self.search_type].search(
                self.index, query["execution"][self.base_execution]["dsl"]
            )
            try:
                total = search_queries.total_hits(response)
            except search_queries.SearchError as exc:
                raise LoadTestError(str(exc)) from exc
            return {
                "result_count": total,
                "internal_request_count": 1,
                "fuzzy_executed": self.search_type == "fuzzy",
            }

        assert self.executor is not None
        futures = {
            method: self.executor.submit(
                self.sessions[method].search,
                self.index,
                query["methods"][method]["dsl"],
            )
            for method in ("keyword", "contain")
        }
        method_results: dict[str, dict[str, Any]] = {}
        for method, future in futures.items():
            response = future.result()
            method_results[method] = {
                "result_count": search_queries.total_hits(response),
                "candidates": hybrid_search._parse_hits(response),
            }
        reasons = hybrid_search.fuzzy_trigger_reasons(
            method_results["keyword"]["candidates"],
            method_results["contain"]["candidates"],
            query,
            self.hybrid_contract["architecture"],
        )
        if reasons:
            response = self.sessions["fuzzy"].search(
                self.index, query["methods"]["fuzzy"]["dsl"]
            )
            method_results["fuzzy"] = {
                "result_count": search_queries.total_hits(response),
                "candidates": hybrid_search._parse_hits(response),
            }
        fused = hybrid_search.fuse_rankings(
            method_results, self.hybrid_contract["architecture"]
        )
        return {
            "result_count": len(fused),
            "internal_request_count": len(method_results),
            "fuzzy_executed": bool(reasons),
            "fuzzy_trigger_reasons": reasons,
        }


def choose_query(
    rng: random.Random, queries: list[dict[str, Any]], weights: list[float]
) -> dict[str, Any]:
    return rng.choices(queries, weights=weights, k=1)[0]


def run_scenario(
    *,
    scenario: dict[str, Any],
    pool: list[dict[str, Any]],
    weights: list[float],
    hybrid_contract: dict[str, Any],
    base_url: str,
    index: str,
    base_seed: int,
    warmup_seconds: float,
    measurement_seconds: float,
    minimum_requests: int,
    timeout: float,
    resource_interval: float,
    base_execution: str = "baseline",
) -> dict[str, Any]:
    client_count = scenario["client_count"]
    scenario_id = scenario["id"]
    search_type = scenario["search_type"]
    warmup_barrier = threading.Barrier(client_count)
    sampler = benchmark.ResourceSampler(base_url, resource_interval, min(timeout, 10.0))
    state: dict[str, float] = {}

    def measurement_action() -> None:
        state["started"] = time.perf_counter()
        sampler.start()

    measurement_barrier = threading.Barrier(client_count, action=measurement_action)
    count_lock = threading.Lock()
    measured_count = 0
    client_outputs: list[dict[str, Any] | None] = [None] * client_count
    fatal_errors: list[str] = []

    def worker(client_id: int) -> None:
        nonlocal measured_count
        client = LoadClient(
            base_url=base_url,
            index=index,
            search_type=search_type,
            timeout=timeout,
            hybrid_contract=hybrid_contract,
            base_execution=base_execution,
        )
        warmup_rng = random.Random(stable_seed(base_seed, scenario_id, client_id, "warmup"))
        measured_seed = stable_seed(base_seed, scenario_id, client_id, "measurement")
        measured_rng = random.Random(measured_seed)
        warmup_count = 0
        warmup_errors = 0
        measurements: list[dict[str, Any]] = []
        try:
            warmup_barrier.wait(timeout=max(30.0, warmup_seconds + 20.0))
            warmup_deadline = time.perf_counter() + warmup_seconds
            while time.perf_counter() < warmup_deadline:
                query = choose_query(warmup_rng, pool, weights)
                try:
                    client.execute(query)
                except Exception:
                    warmup_errors += 1
                warmup_count += 1
            measurement_barrier.wait(timeout=max(30.0, warmup_seconds + 20.0))
            measurement_start = state["started"]
            measurement_deadline = measurement_start + measurement_seconds
            sequence = 0
            while True:
                with count_lock:
                    enough_samples = measured_count >= minimum_requests
                if time.perf_counter() >= measurement_deadline and enough_samples:
                    break
                query = choose_query(measured_rng, pool, weights)
                sequence += 1
                started = time.perf_counter()
                measurement: dict[str, Any] = {
                    "client_id": client_id,
                    "client_sequence": sequence,
                    "query_id": query["id"],
                    "started_offset_seconds": round(started - measurement_start, 6),
                }
                try:
                    execution = client.execute(query)
                    latency_ms = (time.perf_counter() - started) * 1000
                    measurement.update(
                        {
                            "success": True,
                            "client_latency_ms": round(latency_ms, 6),
                            "result_count": execution["result_count"],
                            "internal_request_count": execution["internal_request_count"],
                            "fuzzy_executed": execution["fuzzy_executed"],
                        }
                    )
                except Exception as exc:
                    latency_ms = (time.perf_counter() - started) * 1000
                    measurement.update(
                        {
                            "success": False,
                            "client_latency_ms": round(latency_ms, 6),
                            "internal_request_count": 0,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                measurement["completed_offset_seconds"] = round(
                    time.perf_counter() - measurement_start, 6
                )
                measurements.append(measurement)
                with count_lock:
                    measured_count += 1
            client_outputs[client_id] = {
                "client_id": client_id,
                "measurement_seed": measured_seed,
                "warmup_seed": stable_seed(base_seed, scenario_id, client_id, "warmup"),
                "warmup_request_count": warmup_count,
                "warmup_error_count": warmup_errors,
                "connection_creation_count": client.connection_creations,
                "measurements": measurements,
            }
        except Exception as exc:
            fatal_errors.append(f"client {client_id}: {type(exc).__name__}: {exc}")
            warmup_barrier.abort()
            measurement_barrier.abort()
        finally:
            client.close()

    threads = [
        threading.Thread(target=worker, args=(client_id,), name=f"load-client-{client_id}")
        for client_id in range(client_count)
    ]
    scenario_started_at = benchmark.utc_now()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=warmup_seconds + measurement_seconds + timeout + 60)
    stuck = [thread.name for thread in threads if thread.is_alive()]
    if stuck:
        raise LoadTestError(f"scenario {scenario_id} has stuck workers: {stuck}")
    sampler.stop()
    if fatal_errors:
        raise LoadTestError(f"scenario {scenario_id} worker failure: {fatal_errors}")
    outputs = [output for output in client_outputs if output is not None]
    measurements = [
        measurement for output in outputs for measurement in output["measurements"]
    ]
    measurements.sort(
        key=lambda item: (item["started_offset_seconds"], item["client_id"])
    )
    successful = [item for item in measurements if item["success"]]
    failed = [item for item in measurements if not item["success"]]
    latencies = [float(item["client_latency_ms"]) for item in successful]
    failed_latencies = [float(item["client_latency_ms"]) for item in failed]
    if not measurements or not latencies:
        raise LoadTestError(f"scenario {scenario_id} produced no successful measurements")
    duration = max(float(item["completed_offset_seconds"]) for item in measurements)
    internal_count = sum(int(item["internal_request_count"]) for item in successful)
    resources = benchmark.summarize_resources(sampler.samples)
    per_client_orders = {
        str(output["client_id"]): [
            item["query_id"] for item in output["measurements"]
        ]
        for output in outputs
    }
    recorded_errors = [
        {
            "client_id": item["client_id"],
            "client_sequence": item["client_sequence"],
            "query_id": item["query_id"],
            "error_type": item.get("error_type"),
            "error": item.get("error"),
        }
        for item in failed[:MAX_RECORDED_ERRORS]
    ]
    return {
        "scenario_id": scenario_id,
        "query_id": scenario_id,
        "method": search_type,
        "search_type": search_type,
        "client_count": client_count,
        "started_at": scenario_started_at,
        "finished_at": benchmark.utc_now(),
        "warmup_seconds": warmup_seconds,
        "measurement_target_seconds": measurement_seconds,
        "measurement_duration_seconds": round(duration, 6),
        "minimum_measured_requests": minimum_requests,
        "warmup_request_count": sum(output["warmup_request_count"] for output in outputs),
        "warmup_error_count": sum(output["warmup_error_count"] for output in outputs),
        "measured_request_count": len(measurements),
        "successful_request_count": len(successful),
        "error_count": len(failed),
        "error_rate": round(len(failed) / len(measurements), 9),
        "average_latency_ms": round(statistics.fmean(latencies), 6),
        "p95_latency_ms": round(
            benchmark.percentile_nearest_rank(latencies, 95) or 0, 6
        ),
        "minimum_latency_ms": round(min(latencies), 6),
        "maximum_latency_ms": round(max(latencies), 6),
        "average_failed_latency_ms": (
            round(statistics.fmean(failed_latencies), 6) if failed_latencies else None
        ),
        "user_facing_throughput_per_second": round(len(successful) / duration, 6),
        "internal_elasticsearch_request_count": internal_count,
        "internal_elasticsearch_request_throughput_per_second": round(
            internal_count / duration, 6
        ),
        "result_count": None,
        "result_count_semantics": "mixed fixed query pool; per-request exact counts are recorded",
        "query_selection_counts": {
            query_id: sum(item["query_id"] == query_id for item in measurements)
            for query_id in [query["id"] for query in pool]
        },
        "client_seeds": [
            {
                "client_id": output["client_id"],
                "warmup_seed": output["warmup_seed"],
                "measurement_seed": output["measurement_seed"],
            }
            for output in outputs
        ],
        "per_client_query_order_sha256": {
            client_id: benchmark.canonical_hash(order)
            for client_id, order in per_client_orders.items()
        },
        "per_client_query_order": per_client_orders,
        "connection_creation_count": sum(
            output["connection_creation_count"] for output in outputs
        ),
        "recorded_errors": recorded_errors,
        "errors_truncated": len(failed) > len(recorded_errors),
        "system_metrics": resources,
        "resource_samples": sampler.samples,
        "measurements": measurements,
    }


def evaluate_pressure(scenarios: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    criteria = contract["pressure_evaluation"]
    details: list[dict[str, Any]] = []
    observed = False
    for search_type in SEARCH_TYPES[:3]:
        candidates = [item for item in scenarios if item["search_type"] == search_type]
        one = next((item for item in candidates if item["client_count"] == 1), None)
        highest = max(candidates, key=lambda item: item["client_count"], default=None)
        if one is None or highest is None or highest["client_count"] == 1:
            continue
        average_ratio = highest["average_latency_ms"] / one["average_latency_ms"]
        p95_ratio = highest["p95_latency_ms"] / one["p95_latency_ms"]
        efficiency = (
            highest["user_facing_throughput_per_second"] / highest["client_count"]
        ) / one["user_facing_throughput_per_second"]
        type_observed = (
            average_ratio >= criteria["noticeable_latency_or_p95_ratio"]
            or p95_ratio >= criteria["noticeable_latency_or_p95_ratio"]
            or efficiency <= criteria["maximum_per_client_throughput_efficiency"]
        )
        observed = observed or type_observed
        details.append(
            {
                "search_type": search_type,
                "compared_client_counts": [1, highest["client_count"]],
                "average_latency_ratio": round(average_ratio, 6),
                "p95_latency_ratio": round(p95_ratio, 6),
                "per_client_throughput_efficiency": round(efficiency, 6),
                "pressure_observed": type_observed,
            }
        )
    return {
        "pressure_observed": observed,
        "criteria": criteria,
        "details": details,
        "optional_scenarios_required": not observed,
    }


def write_csv(path: Path, scenarios: list[dict[str, Any]]) -> None:
    fields = [
        "query_id",
        "method",
        "search_type",
        "client_count",
        "measurement_duration_seconds",
        "warmup_request_count",
        "warmup_error_count",
        "measured_request_count",
        "successful_request_count",
        "error_count",
        "error_rate",
        "average_latency_ms",
        "p95_latency_ms",
        "user_facing_throughput_per_second",
        "internal_elasticsearch_request_throughput_per_second",
        "docker_cpu_average_percent",
        "docker_cpu_maximum_percent",
        "docker_memory_average_percent",
        "docker_memory_maximum_percent",
        "jvm_heap_average_bytes",
        "jvm_heap_maximum_bytes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
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


def externalize_measurements(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Write every raw request to deterministic gzip JSONL and slim the summary report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    total = 0
    scenario_counts: dict[str, int] = {}
    with temporary.open("wb") as raw_file:
        with gzip.GzipFile(
            filename="", fileobj=raw_file, mode="wb", compresslevel=6, mtime=0
        ) as compressed:
            for scenario in report["scenarios"]:
                scenario_id = scenario["scenario_id"]
                measurements = scenario.get("measurements", [])
                scenario_counts[scenario_id] = len(measurements)
                for measurement in measurements:
                    record = {"scenario_id": scenario_id, **measurement}
                    compressed.write(
                        (
                            json.dumps(
                                record,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            + "\n"
                        ).encode("utf-8")
                    )
                    total += 1
    temporary.replace(path)
    artifact = {
        "path": str(path),
        "format": "gzip_json_lines",
        "sha256": benchmark.sha256_file(path),
        "size_bytes": path.stat().st_size,
        "record_count": total,
        "scenario_record_counts": scenario_counts,
        "ordering": "scenario order, then request started_offset_seconds/client_id order",
    }
    for scenario in report["scenarios"]:
        scenario["measurements_artifact"] = {
            "path": str(path),
            "sha256": artifact["sha256"],
            "record_count": scenario_counts[scenario["scenario_id"]],
        }
        scenario.pop("measurements", None)
        scenario.pop("per_client_query_order", None)
    report["artifacts"]["raw_measurements"] = artifact
    return artifact


def write_charts(latency_path: Path, throughput_path: Path, scenarios: list[dict[str, Any]]) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise LoadTestError("matplotlib is required for stage-8 charts") from exc
    colors = {"keyword": "#1f77b4", "contain": "#2ca02c", "fuzzy": "#d62728", "hybrid": "#9467bd"}
    latency_path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(11, 7))
    for search_type in SEARCH_TYPES:
        selected = sorted(
            [item for item in scenarios if item["search_type"] == search_type],
            key=lambda item: item["client_count"],
        )
        if not selected:
            continue
        axis.plot(
            [item["client_count"] for item in selected],
            [item["average_latency_ms"] for item in selected],
            marker="o",
            color=colors[search_type],
            label=f"{search_type} average",
        )
        axis.plot(
            [item["client_count"] for item in selected],
            [item["p95_latency_ms"] for item in selected],
            marker="x",
            linestyle="--",
            color=colors[search_type],
            label=f"{search_type} P95",
        )
    axis.set_title("Stage 8 baseline latency under concurrent load")
    axis.set_xlabel("Concurrent clients")
    axis.set_ylabel("End-to-end latency (ms)")
    axis.grid(alpha=0.25)
    axis.legend(ncol=2)
    figure.tight_layout()
    temporary = latency_path.with_name(latency_path.stem + ".tmp" + latency_path.suffix)
    figure.savefig(temporary, dpi=160)
    plt.close(figure)
    temporary.replace(latency_path)

    figure, axis = plt.subplots(figsize=(11, 7))
    for search_type in SEARCH_TYPES:
        selected = sorted(
            [item for item in scenarios if item["search_type"] == search_type],
            key=lambda item: item["client_count"],
        )
        if selected:
            axis.plot(
                [item["client_count"] for item in selected],
                [item["user_facing_throughput_per_second"] for item in selected],
                marker="o",
                color=colors[search_type],
                label=search_type,
            )
    axis.set_title("Stage 8 baseline user-facing throughput")
    axis.set_xlabel("Concurrent clients")
    axis.set_ylabel("Successful user searches/s")
    axis.grid(alpha=0.25)
    axis.legend()
    figure.tight_layout()
    temporary = throughput_path.with_name(
        throughput_path.stem + ".tmp" + throughput_path.suffix
    )
    figure.savefig(temporary, dpi=160)
    plt.close(figure)
    temporary.replace(throughput_path)


def run_load_tests(
    *,
    base_url: str,
    contract_path: Path,
    base_queries_path: Path,
    hybrid_queries_path: Path,
    mapping_path: Path,
    dataset_path: Path,
    ingestion_report_path: Path,
    scenario_ids: list[str] | None,
    warmup_override: float | None,
    duration_override: float | None,
    minimum_requests_override: int | None,
    allow_nonstandard: bool,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    pools, hybrid_contract = load_query_pools(
        contract, base_queries_path, hybrid_queries_path
    )
    defaults = contract["defaults"]
    warmup = warmup_override if warmup_override is not None else defaults["warmup_seconds"]
    duration = duration_override if duration_override is not None else defaults["measurement_seconds"]
    minimum_requests = (
        minimum_requests_override
        if minimum_requests_override is not None
        else defaults["minimum_measured_requests"]
    )
    selected = list(contract["mandatory_scenarios"])
    if scenario_ids:
        wanted = set(scenario_ids)
        known = {scenario["id"] for scenario in selected}
        missing = wanted - known
        if missing:
            raise LoadTestError(f"unknown scenario ids: {sorted(missing)}")
        selected = [scenario for scenario in selected if scenario["id"] in wanted]
    compliant = (
        not scenario_ids
        and warmup >= defaults["warmup_seconds"]
        and duration >= defaults["measurement_seconds"]
        and minimum_requests >= defaults["minimum_measured_requests"]
    )
    if not compliant and not allow_nonstandard:
        raise LoadTestError("non-standard/subset load tests require --allow-nonstandard")
    runtime = benchmark.inspect_runtime(base_url, defaults["index"], defaults["request_timeout_seconds"])
    if runtime["index"]["document_count"] != 50_000:
        raise LoadTestError("stage-8 baseline index must contain exactly 50,000 documents")
    ingestion = json.loads(ingestion_report_path.read_text(encoding="utf-8"))
    if ingestion.get("status") != "passed" or ingestion.get("index") != defaults["index"]:
        raise LoadTestError("ingestion report does not match the baseline index")

    started_at = benchmark.utc_now()
    scenarios: list[dict[str, Any]] = []
    for scenario in selected:
        definition = contract["query_pools"][scenario["search_type"]]
        scenarios.append(
            run_scenario(
                scenario=scenario,
                pool=pools[scenario["search_type"]],
                weights=definition["weights"],
                hybrid_contract=hybrid_contract,
                base_url=base_url,
                index=defaults["index"],
                base_seed=defaults["base_seed"],
                warmup_seconds=warmup,
                measurement_seconds=duration,
                minimum_requests=minimum_requests,
                timeout=defaults["request_timeout_seconds"],
                resource_interval=defaults["resource_sample_interval_seconds"],
                base_execution=defaults.get("base_execution", "baseline"),
            )
        )
        current = scenarios[-1]
        print(
            f"completed {current['scenario_id']}: avg={current['average_latency_ms']}ms "
            f"p95={current['p95_latency_ms']}ms qps={current['user_facing_throughput_per_second']} "
            f"errors={current['error_count']}",
            flush=True,
        )

    pressure = evaluate_pressure(scenarios, contract)
    optional_executed: list[str] = []
    if compliant and not pressure["pressure_observed"]:
        fuzzy_definition = contract["query_pools"]["fuzzy"]
        for client_count in contract["pressure_evaluation"]["optional_client_counts_if_not_observed"]:
            optional = {
                "id": f"fuzzy_clients_{client_count}_optional",
                "search_type": "fuzzy",
                "client_count": client_count,
            }
            scenarios.append(
                run_scenario(
                    scenario=optional,
                    pool=pools["fuzzy"],
                    weights=fuzzy_definition["weights"],
                    hybrid_contract=hybrid_contract,
                    base_url=base_url,
                    index=defaults["index"],
                    base_seed=defaults["base_seed"],
                    warmup_seconds=warmup,
                    measurement_seconds=duration,
                    minimum_requests=minimum_requests,
                    timeout=defaults["request_timeout_seconds"],
                    resource_interval=defaults["resource_sample_interval_seconds"],
                    base_execution=defaults.get("base_execution", "baseline"),
                )
            )
            optional_executed.append(optional["id"])
            pressure = evaluate_pressure(scenarios, contract)
            if pressure["pressure_observed"]:
                break
    pressure["optional_scenarios_executed"] = optional_executed
    pressure["optional_scenarios_not_run_reason"] = (
        "noticeable pressure was already observed in the mandatory 1/5/10-client matrix"
        if compliant and not optional_executed
        else None
    )
    mandatory_ids = {scenario["id"] for scenario in contract["mandatory_scenarios"]}
    completed_ids = {scenario["scenario_id"] for scenario in scenarios}
    mandatory_complete = mandatory_ids.issubset(completed_ids) if compliant else False
    errors = sum(scenario["error_count"] for scenario in scenarios)
    warmup_errors = sum(scenario["warmup_error_count"] for scenario in scenarios)
    resources_complete = all(
        scenario["system_metrics"]["docker_cpu_percent"]["sample_count"] > 0
        and scenario["system_metrics"]["elasticsearch_jvm_heap_used_bytes"]["sample_count"] > 0
        for scenario in scenarios
    )
    status = "passed" if errors == 0 and warmup_errors == 0 and resources_complete else "failed"
    return {
        "status": status,
        "benchmark_type": "load_test",
        "protocol_compliant": compliant,
        "mandatory_scenarios_complete": mandatory_complete,
        "host": base_url,
        "index": defaults["index"],
        "started_at": started_at,
        "finished_at": benchmark.utc_now(),
        "protocol": {
            **defaults,
            "actual_warmup_seconds": warmup,
            "actual_measurement_seconds": duration,
            "actual_minimum_measured_requests": minimum_requests,
            "query_pool_contract": contract["query_pools"],
        },
        "artifacts": {
            "load_contract_path": str(contract_path),
            "load_contract_sha256": benchmark.sha256_file(contract_path),
            "base_queries_path": str(base_queries_path),
            "base_queries_sha256": benchmark.sha256_file(base_queries_path),
            "hybrid_queries_path": str(hybrid_queries_path),
            "hybrid_queries_sha256": benchmark.sha256_file(hybrid_queries_path),
            "mapping_path": str(mapping_path),
            "mapping_sha256": benchmark.sha256_file(mapping_path),
            "dataset_path": str(dataset_path),
            "dataset_sha256": benchmark.sha256_file(dataset_path),
            "ingestion_report_path": str(ingestion_report_path),
            "ingestion_report_sha256": benchmark.sha256_file(ingestion_report_path),
        },
        "runtime": runtime,
        "totals": {
            "scenario_count": len(scenarios),
            "mandatory_scenario_count": len(mandatory_ids & completed_ids),
            "warmup_request_count": sum(item["warmup_request_count"] for item in scenarios),
            "warmup_error_count": warmup_errors,
            "measured_request_count": sum(item["measured_request_count"] for item in scenarios),
            "successful_request_count": sum(item["successful_request_count"] for item in scenarios),
            "internal_elasticsearch_request_count": sum(
                item["internal_elasticsearch_request_count"] for item in scenarios
            ),
            "error_count": errors,
        },
        "pressure_evaluation": pressure,
        "scenarios": scenarios,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("ELASTICSEARCH_URL", search_queries.DEFAULT_ES_URL))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--base-queries", type=Path, default=DEFAULT_BASE_QUERIES)
    parser.add_argument("--hybrid-queries", type=Path, default=DEFAULT_HYBRID_QUERIES)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--ingestion-report", type=Path, default=DEFAULT_INGESTION_REPORT)
    parser.add_argument("--scenario-id", action="append", dest="scenario_ids")
    parser.add_argument("--warmup-seconds", type=float)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--minimum-requests", type=int)
    parser.add_argument("--allow-nonstandard", action="store_true")
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--measurements", type=Path, default=DEFAULT_MEASUREMENTS)
    parser.add_argument("--csv-report", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--latency-chart", type=Path, default=DEFAULT_LATENCY_CHART)
    parser.add_argument("--throughput-chart", type=Path, default=DEFAULT_THROUGHPUT_CHART)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        report = run_load_tests(
            base_url=args.host,
            contract_path=args.contract,
            base_queries_path=args.base_queries,
            hybrid_queries_path=args.hybrid_queries,
            mapping_path=args.mapping,
            dataset_path=args.dataset,
            ingestion_report_path=args.ingestion_report,
            scenario_ids=args.scenario_ids,
            warmup_override=args.warmup_seconds,
            duration_override=args.duration_seconds,
            minimum_requests_override=args.minimum_requests,
            allow_nonstandard=args.allow_nonstandard,
        )
        measurements = externalize_measurements(args.measurements, report)
        benchmark.atomic_write_json(args.json_report, report)
        write_csv(args.csv_report, report["scenarios"])
        write_charts(args.latency_chart, args.throughput_chart, report["scenarios"])
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "protocol_compliant": report["protocol_compliant"],
                    "mandatory_scenarios_complete": report["mandatory_scenarios_complete"],
                    "json_report": str(args.json_report),
                    "measurements": measurements,
                    "csv_report": str(args.csv_report),
                    "latency_chart": str(args.latency_chart),
                    "throughput_chart": str(args.throughput_chart),
                    "totals": report["totals"],
                    "pressure_evaluation": report["pressure_evaluation"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if report["status"] == "passed" else 1
    except (LoadTestError, benchmark.BenchmarkError, search_queries.SearchError, OSError, json.JSONDecodeError) as exc:
        print(f"load-test error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
