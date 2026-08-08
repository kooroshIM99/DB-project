#!/usr/bin/env python3
"""Verify Elasticsearch, both main indexes, document counts, and a real search."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_HOST = "http://127.0.0.1:9200"
DEFAULT_INDEXES = ("arxiv_papers_baseline", "arxiv_papers_optimized")
EXPECTED_COUNT = 50_000
EXPECTED_VERSION = "9.4.4"
DEFAULT_OUTPUT = Path("results/smoke_test.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def request_json(host: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{host.rstrip('/')}{path}", data=payload, method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"{method} {path} returned non-object JSON")
    return result


def run(host: str, indexes: tuple[str, ...], expected_count: int) -> dict[str, Any]:
    root = request_json(host, "GET", "/")
    health = request_json(host, "GET", "/_cluster/health")
    version = root.get("version", {}).get("number")
    checks: list[dict[str, Any]] = []
    query = {
        "size": 1,
        "track_total_hits": True,
        "query": {"match": {"title_abstract": "query optimization"}},
    }
    for index in indexes:
        encoded = urllib.parse.quote(index, safe="")
        count = request_json(host, "GET", f"/{encoded}/_count").get("count")
        response = request_json(host, "POST", f"/{encoded}/_search", query)
        total = response.get("hits", {}).get("total", {})
        total_hits = total.get("value") if isinstance(total, dict) else total
        checks.append({
            "index": index,
            "document_count": count,
            "expected_document_count": expected_count,
            "document_count_passed": count == expected_count,
            "query_total_hits": total_hits,
            "query_returned_hits": len(response.get("hits", {}).get("hits", [])),
            "query_passed": isinstance(total_hits, int) and total_hits > 0,
        })
    passed = (
        version == EXPECTED_VERSION
        and health.get("status") in {"green", "yellow"}
        and all(item["document_count_passed"] and item["query_passed"] for item in checks)
    )
    return {
        "status": "passed" if passed else "failed",
        "checked_at": utc_now(),
        "host": host,
        "cluster_name": root.get("cluster_name"),
        "cluster_health": health.get("status"),
        "elasticsearch_version": version,
        "expected_elasticsearch_version": EXPECTED_VERSION,
        "query": query,
        "query_sha256": hashlib.sha256(json.dumps(query, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--index", action="append", dest="indexes")
    parser.add_argument("--expected-count", type=int, default=EXPECTED_COUNT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = run(args.host, tuple(args.indexes or DEFAULT_INDEXES), args.expected_count)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(args.output)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "passed" else 1
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
