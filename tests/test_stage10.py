from __future__ import annotations

import json
from pathlib import Path

from scripts import build_reproducibility_manifest, smoke_test


def test_smoke_test_checks_both_indexes_and_real_query(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(host: str, method: str, path: str, body=None):
        calls.append((method, path))
        if path == "/":
            return {"cluster_name": "db-project-es", "version": {"number": "9.4.4"}}
        if path == "/_cluster/health":
            return {"status": "green"}
        if path.endswith("/_count"):
            return {"count": 50_000}
        return {"hits": {"total": {"value": 12, "relation": "eq"}, "hits": [{"_id": "one"}]}}

    monkeypatch.setattr(smoke_test, "request_json", fake_request)
    report = smoke_test.run(
        "http://127.0.0.1:9200",
        ("arxiv_papers_baseline", "arxiv_papers_optimized"),
        50_000,
    )
    assert report["status"] == "passed"
    assert len(report["checks"]) == 2
    assert all(item["document_count_passed"] and item["query_passed"] for item in report["checks"])
    assert sum(path.endswith("/_search") for _, path in calls) == 2


def test_manifest_dataset_contract_matches_documentation() -> None:
    documented = Path("dataset/DATASET.md").read_text(encoding="utf-8")
    assert build_reproducibility_manifest.DATASET_SOURCE in documented
    for path, expected in build_reproducibility_manifest.EXPECTED_DATASETS.items():
        assert Path(path).stat().st_size == expected["size_bytes"]
        assert build_reproducibility_manifest.sha256(Path(path)) == expected["sha256"]


def test_stage10_delivery_files_are_declared() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    assert requirements == ["matplotlib==3.10.7", "pytest==9.1.1"]
    assert "README.md" in build_reproducibility_manifest.ESSENTIAL_ARTIFACTS
    assert len(build_reproducibility_manifest.BENCHMARK_REPORTS) == 6
