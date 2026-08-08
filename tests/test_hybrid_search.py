from __future__ import annotations

import threading
from pathlib import Path

import pytest

from scripts import hybrid_search


CONTRACT_PATH = Path("queries/hybrid_comparison_queries.json")


def make_hits(prefix: str, count: int, *, start: int = 1) -> list[dict]:
    return [
        {
            "rank": rank,
            "paper_id": f"{prefix}-{number}",
            "title": f"Title {prefix}-{number}",
            "abstract": f"Abstract {prefix}-{number}",
            "elasticsearch_score": float(count - rank),
        }
        for rank, number in enumerate(range(start, start + count), start=1)
    ]


def base_result(method: str, hits: list[dict]) -> dict:
    return {
        "method": method,
        "result_count": len(hits),
        "candidate_count": len(hits),
        "internal_request_count": 1,
        "fuzzy_executed": method == "fuzzy",
        "fuzzy_trigger_reasons": [],
        "candidates": hits,
        "hits": hits[:10],
    }


def test_contract_fixes_required_queries_weights_and_thresholds() -> None:
    contract = hybrid_search.load_contract(CONTRACT_PATH)

    assert [query["user_input"] for query in contract["queries"]] == [
        "learned indexes",
        "query optimization",
        "databse optimiztion",
    ]
    assert contract["architecture"]["rrf_k"] == 60
    assert contract["architecture"]["weights"] == {
        "keyword": 1.0,
        "contain": 0.9,
        "fuzzy": 0.65,
    }
    assert contract["architecture"]["weights_and_thresholds_tuned_on_comparison_queries"] is False
    assert all(
        query["methods"][method]["dsl"]["track_total_hits"] is True
        for query in contract["queries"]
        for method in hybrid_search.METHODS[:3]
    )


def test_fuzzy_trigger_uses_only_prefuzzy_evidence() -> None:
    contract = hybrid_search.load_contract(CONTRACT_PATH)
    query = contract["queries"][0]
    architecture = contract["architecture"]

    sparse_reasons = hybrid_search.fuzzy_trigger_reasons(
        make_hits("keyword", 5), make_hits("contain", 4), query, architecture
    )
    assert "unique_candidates_below_20" in sparse_reasons
    assert "no_shared_document_in_both_top_10_lists" in sparse_reasons

    shared = make_hits("shared", 20)
    assert hybrid_search.fuzzy_trigger_reasons(shared, shared, query, architecture) == []

    typo_query = contract["queries"][2]
    assert "mandatory_typo_scenario" in hybrid_search.fuzzy_trigger_reasons(
        shared, shared, typo_query, architecture
    )


def test_rrf_deduplicates_by_paper_id_and_uses_fixed_weights() -> None:
    contract = hybrid_search.load_contract(CONTRACT_PATH)
    keyword = make_hits("doc", 2)
    contain = [keyword[1], keyword[0]]
    contain[0] = {**contain[0], "rank": 1}
    contain[1] = {**contain[1], "rank": 2}

    fused = hybrid_search.fuse_rankings(
        {
            "keyword": base_result("keyword", keyword),
            "contain": base_result("contain", contain),
        },
        contract["architecture"],
    )

    assert len(fused) == 2
    expected_first = 1.0 / 61 + 0.9 / 62
    assert fused[0]["paper_id"] == "doc-1"
    assert fused[0]["final_score"] == pytest.approx(expected_first, abs=1e-12)
    assert fused[0]["method_ranks"] == {"keyword": 1, "contain": 2}


def test_hybrid_runs_keyword_and_contain_on_worker_threads_and_skips_fuzzy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = hybrid_search.load_contract(CONTRACT_PATH)
    query = contract["queries"][0]
    shared = make_hits("shared", 20)
    thread_names: dict[str, str] = {}

    def fake_base_method(*, method: str, **_: object) -> dict:
        thread_names[method] = threading.current_thread().name
        if method == "fuzzy":
            raise AssertionError("fuzzy must not run")
        return base_result(method, shared)

    monkeypatch.setattr(hybrid_search, "execute_base_method", fake_base_method)
    result = hybrid_search.execute_hybrid(
        base_url="http://example.invalid",
        index="test",
        query=query,
        architecture=contract["architecture"],
        output_size=10,
        timeout=1,
    )

    assert result["internal_request_count"] == 2
    assert result["fuzzy_executed"] is False
    assert result["fuzzy_trigger_reasons"] == []
    assert thread_names["keyword"].startswith("hybrid-initial")
    assert thread_names["contain"].startswith("hybrid-initial")


def test_typo_hybrid_always_runs_fuzzy(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = hybrid_search.load_contract(CONTRACT_PATH)
    query = contract["queries"][2]
    shared = make_hits("shared", 20)

    def fake_base_method(*, method: str, **_: object) -> dict:
        hits = make_hits("fuzzy", 20) if method == "fuzzy" else shared
        return base_result(method, hits)

    monkeypatch.setattr(hybrid_search, "execute_base_method", fake_base_method)
    result = hybrid_search.execute_hybrid(
        base_url="http://example.invalid",
        index="test",
        query=query,
        architecture=contract["architecture"],
        output_size=10,
        timeout=1,
    )

    assert result["internal_request_count"] == 3
    assert result["fuzzy_executed"] is True
    assert "mandatory_typo_scenario" in result["fuzzy_trigger_reasons"]


def test_stage6_defaults_match_benchmark_protocol() -> None:
    args = hybrid_search.parse_args([])
    assert args.index == "arxiv_papers_baseline"
    assert args.warmups == 5
    assert args.iterations == 30
    assert args.seed == 20250808
    assert args.contract == CONTRACT_PATH
