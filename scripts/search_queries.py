#!/usr/bin/env python3
"""Execute the reproducible stage-5 Elasticsearch query contract.

This is the small, interactive runner.  The full performance baseline, including
warm-up, repeated measurements and resource sampling, lives in benchmark.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ES_URL = "http://127.0.0.1:9200"
DEFAULT_QUERY_FILE = Path("queries/search_queries.json")
REQUIRED_QUERY_TYPES = {"keyword", "contain", "fuzzy", "aggregation"}


class SearchError(RuntimeError):
    """Raised when the query contract or an Elasticsearch request is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_query_contract(path: Path, execution: str = "baseline") -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SearchError(f"cannot read query contract {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SearchError(f"invalid query contract JSON in {path}: {exc}") from exc

    if not isinstance(contract, dict) or not isinstance(contract.get("queries"), list):
        raise SearchError("query contract must be an object containing a queries array")
    defaults = contract.get("defaults")
    if not isinstance(defaults, dict) or not isinstance(defaults.get("index"), str):
        raise SearchError("query contract defaults.index must be a string")

    seen: set[str] = set()
    for position, query in enumerate(contract["queries"], start=1):
        if not isinstance(query, dict):
            raise SearchError(f"query {position} is not an object")
        query_id = query.get("id")
        query_type = query.get("type")
        if not isinstance(query_id, str) or not query_id or query_id in seen:
            raise SearchError(f"query {position} has an invalid or duplicate id")
        seen.add(query_id)
        if query_type not in REQUIRED_QUERY_TYPES:
            raise SearchError(f"query {query_id!r} has unsupported type {query_type!r}")
        if not isinstance(query.get("intent"), str) or "user_input" not in query:
            raise SearchError(f"query {query_id!r} has no logical intent/user_input")
        implementation = query.get("execution", {}).get(execution)
        if not isinstance(implementation, dict) or not isinstance(implementation.get("dsl"), dict):
            raise SearchError(f"query {query_id!r} has no {execution!r} DSL")
        dsl = implementation["dsl"]
        if dsl.get("track_total_hits") is not True:
            raise SearchError(f"query {query_id!r} must set track_total_hits=true")
        if not isinstance(dsl.get("size"), int) or dsl["size"] < 0:
            raise SearchError(f"query {query_id!r} must set a non-negative integer size")
        if not isinstance(dsl.get("query"), dict):
            raise SearchError(f"query {query_id!r} must contain a query object")
    return contract


def select_queries(
    contract: dict[str, Any], *, query_ids: list[str] | None = None, query_type: str | None = None
) -> list[dict[str, Any]]:
    queries = contract["queries"]
    if query_type:
        queries = [query for query in queries if query["type"] == query_type]
    if query_ids:
        wanted = set(query_ids)
        known = {query["id"] for query in contract["queries"]}
        missing = wanted - known
        if missing:
            raise SearchError(f"unknown query id(s): {', '.join(sorted(missing))}")
        queries = [query for query in queries if query["id"] in wanted]
    if not queries:
        raise SearchError("query selection is empty")
    return queries


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=payload,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SearchError(f"{method} {path} failed with HTTP {exc.code}: {error_body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SearchError(f"{method} {path} failed: {exc}") from exc
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise SearchError(f"{method} {path} returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SearchError(f"{method} {path} returned non-object JSON")
    return parsed


def total_hits(response: dict[str, Any]) -> int:
    try:
        total = response["hits"]["total"]
        if isinstance(total, dict):
            if total.get("relation") != "eq":
                raise SearchError("Elasticsearch did not return an exact total hit count")
            return int(total["value"])
        return int(total)
    except (KeyError, TypeError, ValueError) as exc:
        raise SearchError(f"search response has no valid total hit count: {exc}") from exc


def compact_response(response: dict[str, Any]) -> dict[str, Any]:
    hits = response.get("hits", {}).get("hits", [])
    result: dict[str, Any] = {
        "elasticsearch_took_ms": response.get("took"),
        "timed_out": response.get("timed_out"),
        "total_hits": total_hits(response),
        "hits": [
            {
                "rank": rank,
                "paper_id": hit.get("_source", {}).get("paper_id", hit.get("_id")),
                "score": hit.get("_score"),
                "title": hit.get("_source", {}).get("title"),
            }
            for rank, hit in enumerate(hits, start=1)
        ],
    }
    if "aggregations" in response:
        result["aggregations"] = response["aggregations"]
    return result


def execute_query(
    *,
    base_url: str,
    index: str,
    query: dict[str, Any],
    execution: str = "baseline",
    timeout: float = 10.0,
) -> tuple[dict[str, Any], float]:
    encoded_index = urllib.parse.quote(index, safe="")
    started = time.perf_counter_ns()
    response = request_json(
        base_url,
        "POST",
        f"/{encoded_index}/_search",
        body=query["execution"][execution]["dsl"],
        timeout=timeout,
    )
    latency_ms = (time.perf_counter_ns() - started) / 1_000_000
    if response.get("timed_out") is True:
        raise SearchError(f"query {query['id']!r} timed out inside Elasticsearch")
    total_hits(response)
    return response, latency_ms


def run_once(
    *,
    base_url: str,
    index: str,
    queries: list[dict[str, Any]],
    execution: str,
    timeout: float,
) -> dict[str, Any]:
    started_at = utc_now()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for query in queries:
        try:
            response, latency_ms = execute_query(
                base_url=base_url,
                index=index,
                query=query,
                execution=execution,
                timeout=timeout,
            )
            results.append(
                {
                    "query_id": query["id"],
                    "type": query["type"],
                    "intent": query["intent"],
                    "user_input": query["user_input"],
                    "client_latency_ms": round(latency_ms, 6),
                    **compact_response(response),
                }
            )
        except SearchError as exc:
            errors.append({"query_id": query["id"], "error": str(exc)})
    return {
        "status": "passed" if not errors else "failed",
        "benchmark_type": "single_search_run",
        "is_performance_baseline": False,
        "host": base_url,
        "index": index,
        "execution": execution,
        "started_at": started_at,
        "finished_at": utc_now(),
        "successful_request_count": len(results),
        "error_count": len(errors),
        "results": results,
        "errors": errors,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("ELASTICSEARCH_URL", DEFAULT_ES_URL))
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_FILE)
    parser.add_argument("--execution", default="baseline")
    parser.add_argument("--index", help="override defaults.index from the contract")
    parser.add_argument("--query-id", action="append", dest="query_ids")
    parser.add_argument("--type", choices=sorted(REQUIRED_QUERY_TYPES), dest="query_type")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.timeout <= 0:
            raise SearchError("timeout must be positive")
        contract = load_query_contract(args.queries, args.execution)
        selected = select_queries(
            contract, query_ids=args.query_ids, query_type=args.query_type
        )
        index = args.index or contract["defaults"]["index"]
        report = run_once(
            base_url=args.host,
            index=index,
            queries=selected,
            execution=args.execution,
            timeout=args.timeout,
        )
        rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if report["status"] == "passed" else 1
    except SearchError as exc:
        print(f"search error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
