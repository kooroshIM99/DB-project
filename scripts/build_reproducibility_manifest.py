#!/usr/bin/env python3
"""Build and validate the final stage-10 reproducibility manifest."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT = Path("results/reproducibility_manifest.json")
DATASET_SOURCE = "https://www.kaggle.com/datasets/Cornell-University/arxiv"
EXPECTED_DATASETS = {
    "dataset/arxiv_project_sample_50k.jsonl": {
        "size_bytes": 141_586_787,
        "sha256": "190b0a678f946338d9daa100a193e8b9ea59c56cf14157f288ee77b05cf03f84",
    },
    "dataset/arxiv_project_sample_50k_cleaned.jsonl": {
        "size_bytes": 141_586_775,
        "sha256": "86b9febd7fc85d1b9c97377b36db525391854c134430b85e30c87a1dc18f2ad6",
    },
}
ESSENTIAL_ARTIFACTS = [
    "README.md", "requirements.txt", "docker-compose.yml", "dataset/DATASET.md",
    "elasticsearch/README.md", "reports/final-report.md",
    "scripts/smoke_test.py", "scripts/build_reproducibility_manifest.py",
    "scripts/generate_final_report.py", "results/smoke_test.json",
    "elasticsearch/mappings/arxiv_papers_baseline.json",
    "elasticsearch/mappings/arxiv_papers_optimized.json",
    "queries/search_queries.json", "queries/search_queries_optimized.json",
    "queries/hybrid_comparison_queries.json", "queries/hybrid_comparison_queries_optimized.json",
    "queries/load_test_scenarios.json", "queries/load_test_scenarios_optimized.json",
    "results/search_baseline.json", "results/search_optimized.json",
    "results/hybrid_comparison.json", "results/hybrid_comparison_optimized.json",
    "results/hybrid_quality.json", "results/hybrid_quality_optimized.json",
    "results/load_test_baseline.json", "results/load_test_optimized.json",
    "results/load_test_measurements.jsonl.gz", "results/load_test_optimized_measurements.jsonl.gz",
    "results/stage9_ablations.json", "results/stage9_comparison.json",
    "results/metrics_summary.json", "results/metrics_summary.csv",
    "results/stage9_search_before_after.png", "results/stage9_load_before_after.png",
]
BENCHMARK_REPORTS = [
    "results/search_baseline.json", "results/search_optimized.json",
    "results/hybrid_comparison.json", "results/hybrid_comparison_optimized.json",
    "results/load_test_baseline.json", "results/load_test_optimized.json",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def artifact(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_file():
        raise FileNotFoundError(f"required artifact missing: {path}")
    return {"path": path_text, "size_bytes": path.stat().st_size, "sha256": sha256(path)}


def verify_recorded_file(recorded_path: Any, recorded_hash: Any) -> bool | None:
    if not isinstance(recorded_path, str) or not isinstance(recorded_hash, str):
        return None
    path = Path(recorded_path)
    return path.is_file() and sha256(path) == recorded_hash


def traceability(path_text: str) -> dict[str, Any]:
    report = json.loads(Path(path_text).read_text(encoding="utf-8"))
    artifacts = report.get("artifacts", {})
    checks = {}
    for prefix in ("mapping", "dataset", "query_contract", "contract", "load_contract", "base_queries", "hybrid_queries", "ingestion_report"):
        path_key, hash_key = f"{prefix}_path", f"{prefix}_sha256"
        if path_key in artifacts or hash_key in artifacts:
            checks[prefix] = verify_recorded_file(artifacts.get(path_key), artifacts.get(hash_key))
    measurement_refs = [
        scenario.get("measurements_artifact")
        for scenario in report.get("scenarios", [])
        if isinstance(scenario.get("measurements_artifact"), dict)
    ]
    if measurement_refs:
        checks["measurements"] = all(
            verify_recorded_file(reference.get("path"), reference.get("sha256")) is True
            for reference in measurement_refs
        )
    protocol = report.get("protocol", {})
    seed = protocol.get("query_scenario_order_seed", protocol.get("scenario_order_seed", protocol.get("base_seed")))
    container = report.get("runtime", {}).get("container", {})
    complete = (
        report.get("status") == "passed"
        and report.get("protocol_compliant") is True
        and isinstance(report.get("index"), str)
        and checks
        and all(value is True for value in checks.values())
        and seed is not None
    )
    return {
        "path": path_text,
        "sha256": sha256(Path(path_text)),
        "benchmark_type": report.get("benchmark_type"),
        "index": report.get("index"),
        "seed": seed,
        "elasticsearch_version": report.get("runtime", {}).get("elasticsearch_version"),
        "number_of_shards": report.get("runtime", {}).get("index", {}).get("number_of_shards"),
        "number_of_replicas": report.get("runtime", {}).get("index", {}).get("number_of_replicas"),
        "container_memory_limit_bytes": container.get("memory_limit_bytes"),
        "container_cpu_limit": container.get("cpu_limit"),
        "artifact_hash_checks": checks,
        "traceability_complete": complete,
    }


def main() -> int:
    dataset_records = []
    for path_text, expected in EXPECTED_DATASETS.items():
        item = artifact(path_text)
        item["expected"] = expected
        item["verified"] = item["size_bytes"] == expected["size_bytes"] and item["sha256"] == expected["sha256"]
        dataset_records.append(item)
    traceability_records = [traceability(path) for path in BENCHMARK_REPORTS]
    manifest = {
        "status": "passed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "dataset": {
            "upstream_source": DATASET_SOURCE,
            "upstream_license": "CC0 1.0 metadata (per upstream data card)",
            "sample_provenance_limitation": "The supplied repository does not contain the sampling seed or extraction code for the local 50k sample.",
            "files": dataset_records,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "docker": command_version(["docker", "version", "--format", "{{.Server.Version}}"]),
            "docker_compose": command_version(["docker", "compose", "version", "--short"]),
            "elasticsearch": "9.4.4",
            "matplotlib": command_version([sys.executable, "-c", "import matplotlib; print(matplotlib.__version__)"]),
            "pytest": command_version([sys.executable, "-c", "import pytest; print(pytest.__version__)"]),
            "fixed_resources": {"container_memory": "2g", "cpu_limit": 2.0, "heap": "1g", "replicas": 0},
        },
        "essential_artifacts": [artifact(path) for path in ESSENTIAL_ARTIFACTS],
        "benchmark_traceability": traceability_records,
        "validation": {
            "all_datasets_verified": all(item["verified"] for item in dataset_records),
            "all_benchmarks_traceable": all(item["traceability_complete"] for item in traceability_records),
            "essential_artifact_count": len(ESSENTIAL_ARTIFACTS),
        },
    }
    if not all(manifest["validation"].values()):
        manifest["status"] = "failed"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps({"status": manifest["status"], **manifest["validation"], "output": str(OUTPUT)}, indent=2))
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
