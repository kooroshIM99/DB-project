#!/usr/bin/env python3
"""Run the stage-6 weighted-RRF hybrid search and four-method comparison."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark, search_queries
except ModuleNotFoundError:  # Direct execution from scripts/.
    import benchmark  # type: ignore[no-redef]
    import search_queries  # type: ignore[no-redef]


DEFAULT_CONTRACT = Path("queries/hybrid_comparison_queries.json")
DEFAULT_MAPPING = Path("elasticsearch/mappings/arxiv_papers_baseline.json")
DEFAULT_DATASET = Path("dataset/arxiv_project_sample_50k_cleaned.jsonl")
DEFAULT_INGESTION_REPORT = Path("results/ingestion_baseline.json")
DEFAULT_JSON_REPORT = Path("results/hybrid_comparison.json")
DEFAULT_PERFORMANCE_CSV = Path("results/hybrid_performance.csv")
DEFAULT_JUDGMENT_TEMPLATE = Path("results/relevance_judgments_template.csv")
BASELINE_INDEX = "arxiv_papers_baseline"
OPTIMIZED_INDEX = "arxiv_papers_optimized"
METHODS = ("keyword", "contain", "fuzzy", "hybrid")
DEFAULT_WARMUPS = 5
DEFAULT_ITERATIONS = 30
DEFAULT_SEED = 20250808


class HybridError(RuntimeError):
    """Raised when the hybrid contract or execution is invalid."""


def load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HybridError(f"cannot read hybrid contract {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HybridError(f"invalid hybrid contract JSON in {path}: {exc}") from exc
    if not isinstance(contract, dict):
        raise HybridError("hybrid contract must be a JSON object")
    defaults = contract.get("defaults")
    architecture = contract.get("architecture")
    queries = contract.get("queries")
    if not isinstance(defaults, dict) or not isinstance(architecture, dict):
        raise HybridError("hybrid contract requires defaults and architecture objects")
    if not isinstance(queries, list) or len(queries) != 3:
        raise HybridError("hybrid contract must contain exactly three comparison queries")
    if defaults.get("track_total_hits") is not True:
        raise HybridError("hybrid contract must require exact total hit counts")
    candidate_size = defaults.get("candidate_size")
    output_size = defaults.get("output_size")
    if not isinstance(candidate_size, int) or not isinstance(output_size, int):
        raise HybridError("candidate_size and output_size must be integers")
    if candidate_size < 20 or not 1 <= output_size <= candidate_size:
        raise HybridError("invalid hybrid candidate/output sizes")
    if architecture.get("rrf_k") != 60:
        raise HybridError("stage-6 RRF k must remain fixed at 60")
    if architecture.get("weights") != {"keyword": 1.0, "contain": 0.9, "fuzzy": 0.65}:
        raise HybridError("stage-6 RRF weights do not match the fixed contract")
    trigger = architecture.get("fuzzy_trigger")
    if not isinstance(trigger, dict) or trigger.get("minimum_unique_candidates") != 20:
        raise HybridError("stage-6 fuzzy candidate threshold must remain fixed at 20")
    if trigger.get("overlap_rank_depth") != 10:
        raise HybridError("stage-6 overlap rank depth must remain fixed at 10")

    seen: set[str] = set()
    typo_count = 0
    for query in queries:
        if not isinstance(query, dict) or not isinstance(query.get("id"), str):
            raise HybridError("every comparison query requires a string id")
        if query["id"] in seen:
            raise HybridError(f"duplicate comparison query id {query['id']!r}")
        seen.add(query["id"])
        if not isinstance(query.get("user_input"), str) or not isinstance(
            query.get("relevance_concept"), str
        ):
            raise HybridError(f"query {query['id']!r} has no input/relevance concept")
        typo_count += bool(query.get("mandatory_typo_scenario"))
        methods = query.get("methods")
        if not isinstance(methods, dict) or set(methods) != set(METHODS[:3]):
            raise HybridError(f"query {query['id']!r} must define three base methods")
        for method in METHODS[:3]:
            implementation = methods[method]
            dsl = implementation.get("dsl") if isinstance(implementation, dict) else None
            if not isinstance(dsl, dict):
                raise HybridError(f"query {query['id']!r}/{method} has no DSL")
            if dsl.get("track_total_hits") is not True or dsl.get("size") != candidate_size:
                raise HybridError(f"query {query['id']!r}/{method} violates count/size contract")
            if not isinstance(dsl.get("query"), dict):
                raise HybridError(f"query {query['id']!r}/{method} has no query object")
    if typo_count != 1:
        raise HybridError("exactly one comparison query must be the mandatory typo scenario")
    return contract


def _parse_hits(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw_hits = response.get("hits", {}).get("hits", [])
    if not isinstance(raw_hits, list):
        raise HybridError("Elasticsearch response contains no hit list")
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rank, raw_hit in enumerate(raw_hits, start=1):
        source = raw_hit.get("_source", {}) if isinstance(raw_hit, dict) else {}
        paper_id = source.get("paper_id") or raw_hit.get("_id")
        if not isinstance(paper_id, str) or not paper_id:
            raise HybridError("Elasticsearch hit contains no paper_id")
        if paper_id in seen:
            raise HybridError(f"duplicate paper_id {paper_id!r} inside one method response")
        seen.add(paper_id)
        hits.append(
            {
                "rank": rank,
                "paper_id": paper_id,
                "title": source.get("title"),
                "abstract": source.get("abstract"),
                "elasticsearch_score": raw_hit.get("_score"),
            }
        )
    return hits


def execute_base_method(
    *,
    base_url: str,
    index: str,
    query: dict[str, Any],
    method: str,
    output_size: int,
    timeout: float,
) -> dict[str, Any]:
    if method not in METHODS[:3]:
        raise HybridError(f"unsupported base method {method!r}")
    encoded_index = urllib.parse.quote(index, safe="")
    started = time.perf_counter_ns()
    response = search_queries.request_json(
        base_url,
        "POST",
        f"/{encoded_index}/_search",
        body=query["methods"][method]["dsl"],
        timeout=timeout,
    )
    latency_ms = (time.perf_counter_ns() - started) / 1_000_000
    if response.get("timed_out") is True:
        raise HybridError(f"{query['id']}/{method} timed out inside Elasticsearch")
    try:
        total = search_queries.total_hits(response)
    except search_queries.SearchError as exc:
        raise HybridError(str(exc)) from exc
    candidates = _parse_hits(response)
    return {
        "query_id": query["id"],
        "user_input": query["user_input"],
        "method": method,
        "client_latency_ms": round(latency_ms, 6),
        "elasticsearch_took_ms": response.get("took"),
        "result_count": total,
        "result_count_semantics": "exact Elasticsearch total hits",
        "candidate_count": len(candidates),
        "internal_request_count": 1,
        "fuzzy_executed": method == "fuzzy",
        "fuzzy_trigger_reasons": [],
        "candidates": candidates,
        "hits": candidates[:output_size],
    }


def fuzzy_trigger_reasons(
    keyword_hits: list[dict[str, Any]],
    contain_hits: list[dict[str, Any]],
    query: dict[str, Any],
    architecture: dict[str, Any],
) -> list[str]:
    trigger = architecture["fuzzy_trigger"]
    keyword_ids = [hit["paper_id"] for hit in keyword_hits]
    contain_ids = [hit["paper_id"] for hit in contain_hits]
    unique_count = len(set(keyword_ids) | set(contain_ids))
    depth = trigger["overlap_rank_depth"]
    overlap = set(keyword_ids[:depth]) & set(contain_ids[:depth])
    reasons: list[str] = []
    if unique_count < trigger["minimum_unique_candidates"]:
        reasons.append("unique_candidates_below_20")
    if not overlap:
        reasons.append("no_shared_document_in_both_top_10_lists")
    if trigger["trigger_for_typo_scenarios"] and query["mandatory_typo_scenario"]:
        reasons.append("mandatory_typo_scenario")
    return reasons


def fuse_rankings(
    method_results: dict[str, dict[str, Any]], architecture: dict[str, Any]
) -> list[dict[str, Any]]:
    k = architecture["rrf_k"]
    weights = architecture["weights"]
    documents: dict[str, dict[str, Any]] = {}
    ranks: dict[str, dict[str, int]] = {}
    for method in METHODS[:3]:
        if method not in method_results:
            continue
        for hit in method_results[method]["candidates"]:
            paper_id = hit["paper_id"]
            documents.setdefault(
                paper_id,
                {
                    "paper_id": paper_id,
                    "title": hit["title"],
                    "abstract": hit["abstract"],
                },
            )
            ranks.setdefault(paper_id, {})[method] = hit["rank"]

    fused: list[dict[str, Any]] = []
    for paper_id, document in documents.items():
        contributions = {
            method: weights[method] / (k + rank)
            for method, rank in ranks[paper_id].items()
        }
        fused.append(
            {
                **document,
                "final_score": round(sum(contributions.values()), 12),
                "method_ranks": ranks[paper_id],
                "rrf_contributions": {
                    method: round(value, 12) for method, value in contributions.items()
                },
            }
        )
    fused.sort(key=lambda hit: (-hit["final_score"], hit["paper_id"]))
    for rank, hit in enumerate(fused, start=1):
        hit["rank"] = rank
    return fused


def execute_hybrid(
    *,
    base_url: str,
    index: str,
    query: dict[str, Any],
    architecture: dict[str, Any],
    output_size: int,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid-initial") as executor:
        futures = {
            method: executor.submit(
                execute_base_method,
                base_url=base_url,
                index=index,
                query=query,
                method=method,
                output_size=output_size,
                timeout=timeout,
            )
            for method in ("keyword", "contain")
        }
        method_results = {method: future.result() for method, future in futures.items()}

    reasons = fuzzy_trigger_reasons(
        method_results["keyword"]["candidates"],
        method_results["contain"]["candidates"],
        query,
        architecture,
    )
    if reasons:
        method_results["fuzzy"] = execute_base_method(
            base_url=base_url,
            index=index,
            query=query,
            method="fuzzy",
            output_size=output_size,
            timeout=timeout,
        )
    fused = fuse_rankings(method_results, architecture)
    latency_ms = (time.perf_counter_ns() - started) / 1_000_000
    source_totals = {
        method: result["result_count"] for method, result in method_results.items()
    }
    return {
        "query_id": query["id"],
        "user_input": query["user_input"],
        "method": "hybrid",
        "client_latency_ms": round(latency_ms, 6),
        "result_count": len(fused),
        "result_count_semantics": "exact unique candidates within configured retrieval windows",
        "candidate_count": len(fused),
        "source_total_hits": source_totals,
        "internal_request_count": len(method_results),
        "fuzzy_executed": "fuzzy" in method_results,
        "fuzzy_trigger_reasons": reasons,
        "initial_methods_executed_in_parallel": True,
        "rrf_k": architecture["rrf_k"],
        "rrf_weights": architecture["weights"],
        "hits": fused[:output_size],
    }


def execute_method(
    *,
    base_url: str,
    index: str,
    query: dict[str, Any],
    method: str,
    contract: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    output_size = contract["defaults"]["output_size"]
    if method == "hybrid":
        return execute_hybrid(
            base_url=base_url,
            index=index,
            query=query,
            architecture=contract["architecture"],
            output_size=output_size,
            timeout=timeout,
        )
    return execute_base_method(
        base_url=base_url,
        index=index,
        query=query,
        method=method,
        output_size=output_size,
        timeout=timeout,
    )


def execute_scenario(
    *,
    base_url: str,
    index: str,
    query: dict[str, Any],
    method: str,
    contract: dict[str, Any],
    warmups: int,
    iterations: int,
    timeout: float,
) -> dict[str, Any]:
    warmup_errors: list[str] = []
    for _ in range(warmups):
        try:
            execute_method(
                base_url=base_url,
                index=index,
                query=query,
                method=method,
                contract=contract,
                timeout=timeout,
            )
        except (HybridError, search_queries.SearchError) as exc:
            warmup_errors.append(str(exc))

    started_at = benchmark.utc_now()
    started_monotonic = time.perf_counter()
    measurements: list[dict[str, Any]] = []
    latencies: list[float] = []
    failed_latencies: list[float] = []
    result_counts: list[int] = []
    fuzzy_decisions: list[bool] = []
    total_internal_requests = 0
    top_hits: list[dict[str, Any]] = []
    result_semantics: str | None = None
    trigger_reasons: list[str] = []
    for request_number in range(1, iterations + 1):
        request_started = time.perf_counter_ns()
        try:
            result = execute_method(
                base_url=base_url,
                index=index,
                query=query,
                method=method,
                contract=contract,
                timeout=timeout,
            )
            latency = float(result["client_latency_ms"])
            latencies.append(latency)
            result_counts.append(result["result_count"])
            fuzzy_decisions.append(result["fuzzy_executed"])
            total_internal_requests += result["internal_request_count"]
            if not top_hits:
                top_hits = result["hits"]
                result_semantics = result["result_count_semantics"]
                trigger_reasons = result["fuzzy_trigger_reasons"]
            measurements.append(
                {
                    "request_number": request_number,
                    "success": True,
                    "client_latency_ms": round(latency, 6),
                    "result_count": result["result_count"],
                    "internal_request_count": result["internal_request_count"],
                    "fuzzy_executed": result["fuzzy_executed"],
                    "fuzzy_trigger_reasons": result["fuzzy_trigger_reasons"],
                }
            )
        except (HybridError, search_queries.SearchError) as exc:
            latency = (time.perf_counter_ns() - request_started) / 1_000_000
            failed_latencies.append(latency)
            measurements.append(
                {
                    "request_number": request_number,
                    "success": False,
                    "client_latency_ms": round(latency, 6),
                    "error": str(exc),
                }
            )
    duration = time.perf_counter() - started_monotonic
    success_count = len(latencies)
    error_count = len(failed_latencies)
    return {
        "query_id": query["id"],
        "user_input": query["user_input"],
        "relevance_concept": query["relevance_concept"],
        "method": method,
        "started_at": started_at,
        "finished_at": benchmark.utc_now(),
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
        "average_latency_ms": round(statistics.fmean(latencies), 6) if latencies else None,
        "p95_latency_ms": (
            round(benchmark.percentile_nearest_rank(latencies, 95) or 0, 6)
            if latencies
            else None
        ),
        "user_facing_throughput_per_second": (
            round(success_count / duration, 6) if duration else None
        ),
        "internal_elasticsearch_request_count": total_internal_requests,
        "internal_elasticsearch_request_throughput_per_second": (
            round(total_internal_requests / duration, 6) if duration else None
        ),
        "average_failed_latency_ms": (
            round(statistics.fmean(failed_latencies), 6) if failed_latencies else None
        ),
        "result_count": result_counts[0] if result_counts else None,
        "result_count_semantics": result_semantics,
        "result_count_consistent": len(set(result_counts)) <= 1,
        "fuzzy_executed": fuzzy_decisions[0] if fuzzy_decisions else None,
        "fuzzy_decision_consistent": len(set(fuzzy_decisions)) <= 1,
        "fuzzy_trigger_reasons": trigger_reasons,
        "top_hits": top_hits,
        "measurements": measurements,
    }


def write_performance_csv(path: Path, scenarios: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "query_id",
        "user_input",
        "method",
        "result_count",
        "result_count_semantics",
        "average_latency_ms",
        "p95_latency_ms",
        "user_facing_throughput_per_second",
        "internal_elasticsearch_request_throughput_per_second",
        "successful_request_count",
        "error_count",
        "error_rate",
        "fuzzy_executed",
        "fuzzy_trigger_reasons",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for scenario in sorted(scenarios, key=lambda item: (item["query_id"], item["method"])):
            writer.writerow(
                {
                    **{field: scenario.get(field) for field in fields},
                    "fuzzy_trigger_reasons": "|".join(scenario["fuzzy_trigger_reasons"]),
                }
            )
    temporary.replace(path)


def write_judgment_template(path: Path, scenarios: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "query",
        "query_id",
        "relevance_concept",
        "method",
        "rank",
        "paper_id",
        "title",
        "abstract",
        "judgment",
        "notes",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for scenario in sorted(scenarios, key=lambda item: (item["query_id"], item["method"])):
            for hit in scenario["top_hits"]:
                writer.writerow(
                    {
                        "query": scenario["user_input"],
                        "query_id": scenario["query_id"],
                        "relevance_concept": scenario["relevance_concept"],
                        "method": scenario["method"],
                        "rank": hit["rank"],
                        "paper_id": hit["paper_id"],
                        "title": hit["title"],
                        "abstract": hit["abstract"],
                        "judgment": "",
                        "notes": "",
                    }
                )
    temporary.replace(path)


def run_comparison(
    *,
    base_url: str,
    index: str,
    contract_file: Path,
    mapping_file: Path,
    dataset: Path,
    ingestion_report: Path,
    warmups: int,
    iterations: int,
    seed: int,
    timeout: float,
    resource_interval: float,
    allow_nonstandard: bool,
) -> dict[str, Any]:
    if warmups < 0 or iterations <= 0 or timeout <= 0 or resource_interval <= 0:
        raise HybridError("warmups must be non-negative and other numeric values positive")
    compliant = (
        index in {BASELINE_INDEX, OPTIMIZED_INDEX}
        and warmups >= DEFAULT_WARMUPS
        and iterations >= DEFAULT_ITERATIONS
    )
    if not compliant and not allow_nonstandard:
        raise HybridError(
            "non-standard parameters require --allow-nonstandard and cannot be a stage-6 baseline"
        )
    contract = load_contract(contract_file)
    runtime = benchmark.inspect_runtime(base_url, index, timeout)
    if runtime["index"]["document_count"] != 50_000:
        raise HybridError("stage-6 baseline index must contain exactly 50,000 documents")
    try:
        ingestion = json.loads(ingestion_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HybridError(f"cannot load ingestion report: {exc}") from exc
    if ingestion.get("status") != "passed" or ingestion.get("index") != index:
        raise HybridError("ingestion report does not match the target baseline index")

    planned = [(query, method) for query in contract["queries"] for method in METHODS]
    random.Random(seed).shuffle(planned)
    sampler = benchmark.ResourceSampler(base_url, resource_interval, min(timeout, 10.0))
    started_at = benchmark.utc_now()
    started = time.perf_counter()
    sampler.start()
    scenarios: list[dict[str, Any]] = []
    try:
        for query, method in planned:
            scenarios.append(
                execute_scenario(
                    base_url=base_url,
                    index=index,
                    query=query,
                    method=method,
                    contract=contract,
                    warmups=warmups,
                    iterations=iterations,
                    timeout=timeout,
                )
            )
    finally:
        sampler.stop()
    benchmark.attach_scenario_resources(scenarios, sampler.samples)
    resources = benchmark.summarize_resources(sampler.samples)
    total_measured = sum(item["measured_request_count"] for item in scenarios)
    total_success = sum(item["successful_request_count"] for item in scenarios)
    total_errors = sum(item["error_count"] for item in scenarios)
    total_warmup_errors = sum(item["warmup_error_count"] for item in scenarios)
    total_internal = sum(item["internal_elasticsearch_request_count"] for item in scenarios)
    consistent = all(
        item["result_count_consistent"] and item["fuzzy_decision_consistent"]
        for item in scenarios
    )
    resource_complete = (
        resources["docker_cpu_percent"]["sample_count"] > 0
        and resources["elasticsearch_jvm_heap_used_bytes"]["sample_count"] > 0
    )
    status = (
        "passed"
        if total_errors == 0 and total_warmup_errors == 0 and consistent and resource_complete
        else "failed"
    )
    return {
        "status": status,
        "benchmark_type": (
            "stage6_hybrid_comparison_optimized"
            if compliant and index == OPTIMIZED_INDEX
            else "stage6_hybrid_comparison"
            if compliant
            else "stage6_diagnostic"
        ),
        "protocol_compliant": compliant,
        "index": index,
        "host": base_url,
        "started_at": started_at,
        "finished_at": benchmark.utc_now(),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "protocol": {
            "client_count": 1,
            "warmups_per_scenario": warmups,
            "measured_iterations_per_scenario": iterations,
            "scenario_count": len(planned),
            "scenario_order_seed": seed,
            "scenario_order": [f"{query['id']}:{method}" for query, method in planned],
            "latency_definition": "end-to-end user request including all parallel/sequential internal requests",
            "user_facing_throughput_definition": "successful user searches / scenario wall time",
            "internal_throughput_definition": "internal Elasticsearch requests / scenario wall time",
            "cache_cleared": False,
            "request_timeout_seconds": timeout,
            "resource_sample_interval_seconds": resource_interval,
            "ingestion_batch_size": ingestion.get("batch_size"),
        },
        "architecture": contract["architecture"],
        "artifacts": {
            "contract_path": str(contract_file),
            "contract_sha256": benchmark.sha256_file(contract_file),
            "mapping_path": str(mapping_file),
            "mapping_sha256": benchmark.sha256_file(mapping_file),
            "dataset_path": str(dataset),
            "dataset_sha256": benchmark.sha256_file(dataset),
            "ingestion_report_path": str(ingestion_report),
            "ingestion_report_sha256": benchmark.sha256_file(ingestion_report),
        },
        "runtime": runtime,
        "totals": {
            "scenario_count": len(scenarios),
            "warmup_user_search_count": warmups * len(scenarios),
            "warmup_error_count": total_warmup_errors,
            "measured_user_search_count": total_measured,
            "successful_user_search_count": total_success,
            "internal_elasticsearch_request_count": total_internal,
            "error_count": total_errors,
            "error_rate": round(total_errors / total_measured, 9),
        },
        "resource_metrics_complete": resource_complete,
        "system_metrics": resources,
        "resource_samples": sampler.samples,
        "scenarios": scenarios,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("ELASTICSEARCH_URL", search_queries.DEFAULT_ES_URL))
    parser.add_argument("--index", default=BASELINE_INDEX)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--ingestion-report", type=Path, default=DEFAULT_INGESTION_REPORT)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--resource-interval", type=float, default=1.0)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--performance-csv", type=Path, default=DEFAULT_PERFORMANCE_CSV)
    parser.add_argument("--judgment-template", type=Path, default=DEFAULT_JUDGMENT_TEMPLATE)
    parser.add_argument("--allow-nonstandard", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        report = run_comparison(
            base_url=args.host,
            index=args.index,
            contract_file=args.contract,
            mapping_file=args.mapping,
            dataset=args.dataset,
            ingestion_report=args.ingestion_report,
            warmups=args.warmups,
            iterations=args.iterations,
            seed=args.seed,
            timeout=args.timeout,
            resource_interval=args.resource_interval,
            allow_nonstandard=args.allow_nonstandard,
        )
        benchmark.atomic_write_json(args.json_report, report)
        write_performance_csv(args.performance_csv, report["scenarios"])
        write_judgment_template(args.judgment_template, report["scenarios"])
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "protocol_compliant": report["protocol_compliant"],
                    "json_report": str(args.json_report),
                    "performance_csv": str(args.performance_csv),
                    "judgment_template": str(args.judgment_template),
                    "totals": report["totals"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if report["status"] == "passed" else 1
    except (HybridError, benchmark.BenchmarkError, search_queries.SearchError, OSError) as exc:
        print(f"hybrid error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
