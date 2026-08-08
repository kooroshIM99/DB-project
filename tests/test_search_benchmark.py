from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import benchmark, search_queries


QUERY_FILE = Path("queries/search_queries.json")


def test_stage5_contract_contains_all_required_queries() -> None:
    contract = search_queries.load_query_contract(QUERY_FILE)
    queries = contract["queries"]

    assert len(queries) == 9
    assert [query["type"] for query in queries].count("keyword") == 3
    assert [query["type"] for query in queries].count("contain") == 3
    assert [query["type"] for query in queries].count("fuzzy") == 2
    assert [query["type"] for query in queries].count("aggregation") == 1
    assert all(
        query["execution"]["baseline"]["dsl"]["track_total_hits"] is True
        for query in queries
    )


def test_category_constraint_uses_filter_context() -> None:
    contract = search_queries.load_query_contract(QUERY_FILE)
    query = next(
        query for query in contract["queries"] if query["id"] == "keyword_learned_indexes_cs_db"
    )
    bool_query = query["execution"]["baseline"]["dsl"]["query"]["bool"]

    assert bool_query["filter"] == [{"term": {"categories": "cs.DB"}}]


def test_contain_contract_keeps_phrase_and_positive_negative_semantics() -> None:
    contract = search_queries.load_query_contract(QUERY_FILE)
    by_id = {query["id"]: query for query in contract["queries"]}

    phrase = by_id["contain_database_systems_phrase"]["execution"]["baseline"]
    positive_negative = by_id["contain_indexing_not_blockchain"]["execution"]["baseline"]
    alternatives = by_id["contain_processing_phrases_any"]["execution"]["baseline"]

    assert phrase["semantics"] == "consecutive_phrase"
    assert phrase["dsl"]["query"]["multi_match"]["type"] == "phrase"
    assert "must" in positive_negative["dsl"]["query"]["bool"]
    assert "must_not" in positive_negative["dsl"]["query"]["bool"]
    assert alternatives["dsl"]["query"]["bool"]["minimum_should_match"] == 1


def test_select_queries_rejects_unknown_id() -> None:
    contract = search_queries.load_query_contract(QUERY_FILE)
    with pytest.raises(search_queries.SearchError, match="unknown query"):
        search_queries.select_queries(contract, query_ids=["does_not_exist"])


def test_contract_validation_rejects_inexact_hit_count(tmp_path: Path) -> None:
    contract = json.loads(QUERY_FILE.read_text(encoding="utf-8"))
    contract["queries"][0]["execution"]["baseline"]["dsl"]["track_total_hits"] = False
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(search_queries.SearchError, match="track_total_hits"):
        search_queries.load_query_contract(path)


def test_nearest_rank_p95() -> None:
    assert benchmark.percentile_nearest_rank(list(range(1, 31)), 95) == 29
    assert benchmark.percentile_nearest_rank([], 95) is None


def test_execute_scenario_excludes_warmups_and_records_each_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = search_queries.load_query_contract(QUERY_FILE)
    query = contract["queries"][0]
    calls = 0

    def fake_execute_query(**_: object) -> tuple[dict, float]:
        nonlocal calls
        calls += 1
        return {
            "took": 1,
            "timed_out": False,
            "hits": {"total": {"value": 7, "relation": "eq"}, "hits": []},
        }, float(calls)

    monkeypatch.setattr(search_queries, "execute_query", fake_execute_query)
    scenario = benchmark.execute_scenario(
        base_url="http://example.invalid",
        index="test-index",
        query=query,
        execution="baseline",
        warmups=2,
        iterations=3,
        timeout=1,
    )

    assert calls == 5
    assert scenario["warmup_request_count"] == 2
    assert scenario["measured_request_count"] == 3
    assert scenario["successful_request_count"] == 3
    assert [item["client_latency_ms"] for item in scenario["measurements"]] == [3.0, 4.0, 5.0]
    assert scenario["average_latency_ms"] == 4.0
    assert scenario["p95_latency_ms"] == 5.0
    assert scenario["result_count"] == 7
    assert scenario["error_rate"] == 0


def test_baseline_defaults_match_stage5_protocol() -> None:
    args = benchmark.parse_args([])
    assert args.index == "arxiv_papers_baseline"
    assert args.warmups == 5
    assert args.iterations == 30
    assert args.seed == 20250808
    assert args.resource_interval == 1.0
    assert args.ingestion_report == Path("results/ingestion_baseline.json")
    assert args.json_report == Path("results/search_baseline.json")
