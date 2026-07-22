#!/usr/bin/env python3
"""Check the local Elasticsearch service configured by docker-compose.yml."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


BASE_URL = "http://127.0.0.1:9200"


def get_json(path: str) -> dict:
    request = urllib.request.Request(f"{BASE_URL}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    try:
        root = get_json("/")
        health = get_json("/_cluster/health")
        nodes = get_json("/_nodes/process,jvm,settings")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2))
        return 1

    summary = {
        "status": "passed",
        "endpoint": BASE_URL,
        "cluster_name": root.get("cluster_name"),
        "cluster_uuid": root.get("cluster_uuid"),
        "version": root.get("version", {}).get("number"),
        "tagline": root.get("tagline"),
        "health": {
            "status": health.get("status"),
            "number_of_nodes": health.get("number_of_nodes"),
            "active_primary_shards": health.get("active_primary_shards"),
            "active_shards": health.get("active_shards"),
            "unassigned_shards": health.get("unassigned_shards"),
        },
        "nodes": {},
    }

    for node_id, node in nodes.get("nodes", {}).items():
        settings = node.get("settings", {})
        jvm = node.get("jvm", {})
        process = node.get("process", {})
        summary["nodes"][node_id] = {
            "name": node.get("name"),
            "cluster_name": settings.get("cluster", {}).get("name"),
            "heap_init": jvm.get("mem", {}).get("heap_init_in_bytes"),
            "heap_max": jvm.get("mem", {}).get("heap_max_in_bytes"),
            "mlockall": process.get("mlockall"),
        }

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if health.get("status") in {"green", "yellow"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
