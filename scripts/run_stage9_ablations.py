#!/usr/bin/env python3
"""Run stage-9 single-variable search and ingestion ablations."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark, create_index, index_dataset, search_queries
except ModuleNotFoundError:
    import benchmark  # type: ignore[no-redef]
    import create_index  # type: ignore[no-redef]
    import index_dataset  # type: ignore[no-redef]
    import search_queries  # type: ignore[no-redef]


BASELINE_INDEX = "arxiv_papers_baseline"
OPTIMIZED_INDEX = "arxiv_papers_optimized"
DEFAULT_OUTPUT = Path("results/stage9_ablations.json")
DEFAULT_DATASET = Path("dataset/arxiv_project_sample_50k_cleaned.jsonl")
DEFAULT_MAPPING = Path("elasticsearch/mappings/arxiv_papers_baseline.json")


def measure(base_url: str, index: str, dsl: dict[str, Any], warmups: int, iterations: int) -> dict[str, Any]:
    latencies: list[float] = []
    counts: list[int] = []
    for position in range(warmups + iterations):
        started = time.perf_counter_ns()
        response = search_queries.request_json(base_url, "POST", f"/{index}/_search", body=dsl)
        latency = (time.perf_counter_ns() - started) / 1_000_000
        if position >= warmups:
            latencies.append(latency)
            counts.append(search_queries.total_hits(response))
    return {
        "warmups": warmups,
        "iterations": iterations,
        "average_latency_ms": round(statistics.fmean(latencies), 6),
        "p95_latency_ms": round(benchmark.percentile_nearest_rank(latencies, 95) or 0, 6),
        "throughput_per_second": round(1000 / statistics.fmean(latencies), 3),
        "result_count": counts[0],
        "result_count_consistent": len(set(counts)) == 1,
    }


def pair(name: str, control: dict[str, Any], treatment: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    control_avg = control["average_latency_ms"]
    treatment_avg = treatment["average_latency_ms"]
    return {
        "name": name,
        **metadata,
        "control": control,
        "treatment": treatment,
        "average_latency_change_percent": round((treatment_avg / control_avg - 1) * 100, 3),
        "result_counts_equal": control["result_count"] == treatment["result_count"],
    }


def search_ablations(base_url: str, warmups: int, iterations: int) -> list[dict[str, Any]]:
    common = {"size": 10, "track_total_hits": True}
    query = "query optimization"
    structure_control = {**common, "query": {"multi_match": {"query": query, "fields": ["title^2", "abstract", "title_abstract"], "type": "best_fields", "operator": "or"}}}
    structure_treatment = {**common, "query": {"multi_match": {"query": query, "fields": ["title^2", "abstract"], "type": "best_fields", "operator": "or"}}}
    analyzer_control = structure_treatment
    analyzer_treatment = {**common, "query": {"multi_match": {"query": query, "fields": ["title.english^2", "abstract.english"], "type": "best_fields", "operator": "or"}}}
    substring_inputs = ["transact", "optimiz", "databas"]
    results = [
        pair(
            "query_structure_remove_duplicate_title_abstract",
            measure(base_url, BASELINE_INDEX, structure_control, warmups, iterations),
            measure(base_url, BASELINE_INDEX, structure_treatment, warmups, iterations),
            isolated_variable="queried fields only; index, analyzer, input, filters, and protocol fixed",
            control_execution="title^2 + abstract + duplicated title_abstract",
            treatment_execution="title^2 + abstract",
        ),
        pair(
            "english_analyzer",
            measure(base_url, OPTIMIZED_INDEX, analyzer_control, warmups, iterations),
            measure(base_url, OPTIMIZED_INDEX, analyzer_treatment, warmups, iterations),
            isolated_variable="standard versus English analyzer multi-fields on the same documents/index",
            control_execution="title^2 + abstract (standard)",
            treatment_execution="title.english^2 + abstract.english",
        ),
    ]
    for text in substring_inputs:
        wildcard = {**common, "query": {"wildcard": {"title_abstract": {"value": f"*{text}*", "case_insensitive": True}}}}
        ngram = {**common, "query": {"match": {"title_abstract.substring": {"query": text, "operator": "and"}}}}
        results.append(
            pair(
                f"substring_wildcard_vs_ngram_{text}",
                measure(base_url, OPTIMIZED_INDEX, wildcard, warmups, iterations),
                measure(base_url, OPTIMIZED_INDEX, ngram, warmups, iterations),
                isolated_variable="wildcard versus n-gram execution on identical source text, input, index, and protocol",
                control_execution="wildcard *input*",
                treatment_execution="match on title_abstract.substring",
            )
        )
    return results


def ingestion_ablations(base_url: str, dataset: Path, mapping_path: Path) -> list[dict[str, Any]]:
    mapping = create_index.read_mapping(mapping_path)
    experiments = [
        ("batch_250", 250, None),
        ("batch_500_control", 500, None),
        ("batch_1000", 1000, None),
        ("refresh_disabled", 500, "-1"),
    ]
    rows: list[dict[str, Any]] = []
    for name, batch_size, refresh_interval in experiments:
        index = f"arxiv_papers_stage9_tmp_{name}"
        if create_index.index_exists(base_url, index):
            create_index.request_json(base_url, "DELETE", f"/{index}")
        create_index.create_or_validate_index(base_url=base_url, index=index, mapping=mapping, recreate=False)
        if refresh_interval:
            create_index.request_json(base_url, "PUT", f"/{index}/_settings", {"index": {"refresh_interval": refresh_interval}})
        with tempfile.TemporaryDirectory(prefix="db-project-stage9-") as directory:
            report = index_dataset.ingest(
                base_url=base_url,
                dataset=dataset,
                report_path=Path(directory) / "ingestion.json",
                batch_size=batch_size,
                expected_count=50_000,
                timeout=60.0,
                index=index,
                benchmark_type="stage9_ingestion_ablation",
            )
        rows.append({
            "name": name,
            "isolated_variable": "batch_size" if refresh_interval is None else "refresh_interval",
            "batch_size": batch_size,
            "refresh_interval_during_ingestion": refresh_interval or "1s (default)",
            "duration_seconds": report["duration_seconds"],
            "documents_per_second": report["documents_per_second"],
            "document_count": report["document_count"],
            "bulk_error_count": report["bulk_error_count"],
        })
        create_index.request_json(base_url, "DELETE", f"/{index}")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=search_queries.DEFAULT_ES_URL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    report = {
        "status": "passed",
        "benchmark_type": "stage9_single_variable_ablations",
        "started_at": benchmark.utc_now(),
        "protocol": {"warmups": args.warmups, "iterations": args.iterations, "dataset_sha256": benchmark.sha256_file(args.dataset)},
        "search_ablations": search_ablations(args.host, args.warmups, args.iterations),
        "ingestion_ablations": ingestion_ablations(args.host, args.dataset, args.mapping),
        "shard_ablation": {
            "status": "not_run",
            "reason": "The fixed dataset is only 50k documents on one constrained single node; extra shards add coordination overhead and are not a useful candidate optimization.",
            "fixed_number_of_shards": 1,
        },
    }
    report["finished_at"] = benchmark.utc_now()
    benchmark.atomic_write_json(args.output, report)
    print(json.dumps({"status": report["status"], "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
