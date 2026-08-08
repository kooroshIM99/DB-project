from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import plot_metrics, run_load_tests


CONTRACT_PATH = Path("queries/load_test_scenarios.json")
OPTIMIZED_CONTRACT_PATH = Path("queries/load_test_scenarios_optimized.json")


def test_contract_contains_exact_mandatory_matrix_and_pools() -> None:
    contract = run_load_tests.load_contract(CONTRACT_PATH)
    scenarios = contract["mandatory_scenarios"]

    assert len(scenarios) == 10
    assert {
        (scenario["search_type"], scenario["client_count"]) for scenario in scenarios
    } == {
        ("keyword", 1),
        ("keyword", 5),
        ("keyword", 10),
        ("contain", 1),
        ("contain", 5),
        ("contain", 10),
        ("fuzzy", 1),
        ("fuzzy", 5),
        ("fuzzy", 10),
        ("hybrid", 10),
    }
    assert contract["query_pools"]["keyword"]["weights"] == [1, 1, 1]
    assert contract["query_pools"]["contain"]["weights"] == [1, 1, 1]
    assert contract["query_pools"]["fuzzy"]["weights"] == [1, 1]
    assert contract["query_pools"]["hybrid"]["weights"] == [1, 1, 1]
    assert contract["defaults"]["warmup_seconds"] == 10.0
    assert contract["defaults"]["measurement_seconds"] == 60.0
    assert contract["defaults"]["request_timeout_seconds"] == 10.0


def test_query_pools_resolve_to_stage5_and_stage6_contracts() -> None:
    contract = run_load_tests.load_contract(CONTRACT_PATH)
    pools, hybrid_contract = run_load_tests.load_query_pools(
        contract,
        Path("queries/search_queries.json"),
        Path("queries/hybrid_comparison_queries.json"),
    )

    assert [query["id"] for query in pools["keyword"]] == contract["query_pools"][
        "keyword"
    ]["query_ids"]
    assert [query["id"] for query in pools["contain"]] == contract["query_pools"][
        "contain"
    ]["query_ids"]
    assert [query["id"] for query in pools["fuzzy"]] == contract["query_pools"][
        "fuzzy"
    ]["query_ids"]
    assert [query["id"] for query in pools["hybrid"]] == contract["query_pools"][
        "hybrid"
    ]["query_ids"]
    assert hybrid_contract["architecture"]["rrf_k"] == 60


def test_stage9_load_contract_replays_same_matrix_with_optimized_execution() -> None:
    baseline = run_load_tests.load_contract(CONTRACT_PATH)
    optimized = run_load_tests.load_contract(OPTIMIZED_CONTRACT_PATH)
    assert optimized["mandatory_scenarios"] == baseline["mandatory_scenarios"]
    assert optimized["query_pools"] == baseline["query_pools"]
    assert optimized["defaults"]["base_seed"] == baseline["defaults"]["base_seed"]
    assert optimized["defaults"]["index"] == "arxiv_papers_optimized"
    assert optimized["defaults"]["base_execution"] == "optimized"
    pools, _ = run_load_tests.load_query_pools(
        optimized,
        Path("queries/search_queries_optimized.json"),
        Path("queries/hybrid_comparison_queries_optimized.json"),
    )
    assert all("optimized" in query["execution"] for query in pools["keyword"])


def test_stable_seed_is_reproducible_and_phase_specific() -> None:
    first = run_load_tests.stable_seed(20250808, "keyword_clients_5", 3, "measurement")
    assert first == run_load_tests.stable_seed(
        20250808, "keyword_clients_5", 3, "measurement"
    )
    assert first != run_load_tests.stable_seed(20250808, "keyword_clients_5", 3, "warmup")
    assert first != run_load_tests.stable_seed(20250808, "keyword_clients_5", 4, "measurement")


def test_persistent_session_reuses_one_http_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        {
            "timed_out": False,
            "hits": {"total": {"value": 1, "relation": "eq"}, "hits": []},
        },
        {
            "timed_out": False,
            "hits": {"total": {"value": 2, "relation": "eq"}, "hits": []},
        },
    ]
    creations = 0

    class FakeResponse:
        status = 200

        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def read(self) -> bytes:
            return json.dumps(self.payload).encode()

    class FakeConnection:
        def __init__(self, *_: object, **__: object) -> None:
            nonlocal creations
            creations += 1

        def request(self, *_: object, **__: object) -> None:
            return None

        def getresponse(self) -> FakeResponse:
            return FakeResponse(responses.pop(0))

        def close(self) -> None:
            return None

    monkeypatch.setattr(run_load_tests.http.client, "HTTPConnection", FakeConnection)
    session = run_load_tests.PersistentElasticsearchSession("http://127.0.0.1:9200", 10)

    assert session.search("index", {"query": {}})["hits"]["total"]["value"] == 1
    assert session.search("index", {"query": {}})["hits"]["total"]["value"] == 2
    assert creations == 1
    assert session.connection_creations == 1


def test_pressure_evaluation_detects_latency_growth_and_efficiency_loss() -> None:
    contract = run_load_tests.load_contract(CONTRACT_PATH)
    scenarios = []
    for search_type in ("keyword", "contain", "fuzzy"):
        scenarios.extend(
            [
                {
                    "search_type": search_type,
                    "client_count": 1,
                    "average_latency_ms": 5.0,
                    "p95_latency_ms": 8.0,
                    "user_facing_throughput_per_second": 100.0,
                },
                {
                    "search_type": search_type,
                    "client_count": 10,
                    "average_latency_ms": 20.0,
                    "p95_latency_ms": 30.0,
                    "user_facing_throughput_per_second": 300.0,
                },
            ]
        )

    result = run_load_tests.evaluate_pressure(scenarios, contract)
    assert result["pressure_observed"] is True
    assert result["optional_scenarios_required"] is False
    assert all(detail["pressure_observed"] for detail in result["details"])


def test_short_scenario_preserves_measurements_seeds_and_barrier_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.calls = 0

        def execute(self, query: dict) -> dict:
            self.calls += 1
            return {
                "result_count": len(query["id"]),
                "internal_request_count": 1,
                "fuzzy_executed": False,
            }

        @property
        def connection_creations(self) -> int:
            return 1

        def close(self) -> None:
            return None

    class FakeSampler:
        def __init__(self, *_: object, **__: object) -> None:
            self.samples = [
                {
                    "sampled_at": "now",
                    "monotonic_seconds": 1.0,
                    "docker": {"cpu_percent": 10.0, "memory_percent": 20.0},
                    "elasticsearch": {
                        "jvm_heap_used_bytes": 1000.0,
                        "jvm_heap_used_percent": 1.0,
                        "process_cpu_percent": 2.0,
                        "os_cpu_percent": 3.0,
                    },
                }
            ]

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    monkeypatch.setattr(run_load_tests, "LoadClient", FakeClient)
    monkeypatch.setattr(run_load_tests.benchmark, "ResourceSampler", FakeSampler)
    scenario = run_load_tests.run_scenario(
        scenario={"id": "test_clients_2", "search_type": "keyword", "client_count": 2},
        pool=[{"id": "one"}, {"id": "two"}],
        weights=[1, 1],
        hybrid_contract={"architecture": {}},
        base_url="http://127.0.0.1:9200",
        index="test",
        base_seed=123,
        warmup_seconds=0.001,
        measurement_seconds=0.005,
        minimum_requests=20,
        timeout=1,
        resource_interval=1,
    )

    assert scenario["client_count"] == 2
    assert scenario["measured_request_count"] >= 20
    assert scenario["successful_request_count"] == scenario["measured_request_count"]
    assert scenario["error_count"] == 0
    assert len(scenario["client_seeds"]) == 2
    assert set(scenario["per_client_query_order"]) == {"0", "1"}
    assert scenario["connection_creation_count"] == 2
    assert scenario["measurement_duration_seconds"] >= 0.005


def test_raw_measurements_are_losslessly_externalized_and_hydrated(tmp_path: Path) -> None:
    measurements = [
        {"success": True, "client_latency_ms": 1.0, "client_id": 0},
        {"success": False, "client_latency_ms": 2.0, "client_id": 1},
    ]
    report = {
        "artifacts": {},
        "scenarios": [
            {
                "scenario_id": "scenario",
                "measurements": list(measurements),
                "per_client_query_order": {"0": ["a"], "1": ["b"]},
            }
        ],
    }
    path = tmp_path / "measurements.jsonl.gz"

    artifact = run_load_tests.externalize_measurements(path, report)
    assert artifact["record_count"] == 2
    assert "measurements" not in report["scenarios"][0]
    assert "per_client_query_order" not in report["scenarios"][0]
    plot_metrics.hydrate_external_measurements(report, tmp_path / "report.json")
    assert report["scenarios"][0]["measurements"] == measurements


def test_cli_defaults_are_full_protocol() -> None:
    args = run_load_tests.parse_args([])
    assert args.contract == CONTRACT_PATH
    assert args.scenario_ids is None
    assert args.warmup_seconds is None
    assert args.duration_seconds is None
    assert args.minimum_requests is None
    assert args.json_report == Path("results/load_test_baseline.json")
    assert args.measurements == Path("results/load_test_measurements.jsonl.gz")
