#!/usr/bin/env python3
"""Create and validate an Elasticsearch index from a mapping file.

The default target is the stage-3 baseline index. The script is intentionally
safe by default: it never deletes an existing index unless --recreate is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_ES_URL = "http://127.0.0.1:9200"
DEFAULT_INDEX = "arxiv_papers_baseline"
DEFAULT_MAPPING = "elasticsearch/mappings/arxiv_papers_baseline.json"
EXPECTED_FIELDS = {
    "paper_id": {"type": "keyword"},
    "title": {"type": "text", "analyzer": "standard"},
    "abstract": {"type": "text", "analyzer": "standard"},
    "title_abstract": {"type": "text", "analyzer": "standard"},
    "authors": {"type": "text", "analyzer": "standard"},
    "categories": {"type": "keyword"},
    "primary_category": {"type": "keyword"},
    "year": {"type": "integer"},
    "update_date": {"type": "date", "format": "yyyy-MM-dd"},
}


class ElasticsearchError(RuntimeError):
    """Raised when Elasticsearch returns an unexpected response."""


def read_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            mapping = json.load(file)
    except FileNotFoundError as exc:
        raise ElasticsearchError(f"mapping file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ElasticsearchError(f"mapping file is not valid JSON: {path}: {exc}") from exc

    validate_mapping_file(mapping)
    return mapping


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_mapping_file(mapping: dict[str, Any]) -> None:
    settings = mapping.get("settings", {}).get("index", {})
    if settings.get("number_of_shards") != 1:
        raise ElasticsearchError("baseline mapping must set number_of_shards to 1")
    if settings.get("number_of_replicas") != 0:
        raise ElasticsearchError("baseline mapping must set number_of_replicas to 0")

    if "analysis" in mapping.get("settings", {}).get("index", {}):
        raise ElasticsearchError("baseline mapping must not define custom analysis")
    if "analysis" in mapping.get("settings", {}):
        raise ElasticsearchError("baseline mapping must not define custom analysis")

    mappings = mapping.get("mappings", {})
    if mappings.get("dynamic") != "strict":
        raise ElasticsearchError('baseline mapping must set mappings.dynamic to "strict"')

    properties = mappings.get("properties")
    if not isinstance(properties, dict):
        raise ElasticsearchError("baseline mapping must define mappings.properties")
    if set(properties) != set(EXPECTED_FIELDS):
        missing = sorted(set(EXPECTED_FIELDS) - set(properties))
        extra = sorted(set(properties) - set(EXPECTED_FIELDS))
        raise ElasticsearchError(f"unexpected baseline fields; missing={missing}, extra={extra}")

    forbidden_tokens = ("ngram", "edge_ngram", "fields")
    serialized = json.dumps(mapping, sort_keys=True)
    for token in forbidden_tokens:
        if token in serialized:
            raise ElasticsearchError(f"baseline mapping must not contain {token}")

    for field, expected in EXPECTED_FIELDS.items():
        actual = properties[field]
        for key, value in expected.items():
            if actual.get(key) != value:
                raise ElasticsearchError(
                    f"field {field!r} must have {key}={value!r}; got {actual.get(key)!r}"
                )


def request_json(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ElasticsearchError(f"{method} {path} failed with HTTP {exc.code}: {error_body}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ElasticsearchError(f"{method} {path} failed: {exc}") from exc


def index_exists(base_url: str, index: str) -> bool:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/{index}", method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise ElasticsearchError(f"HEAD /{index} failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ElasticsearchError(f"HEAD /{index} failed: {exc}") from exc


def create_or_validate_index(
    *,
    base_url: str,
    index: str,
    mapping: dict[str, Any],
    recreate: bool,
) -> dict[str, Any]:
    existed = index_exists(base_url, index)
    recreated = False

    if existed and recreate:
        request_json(base_url, "DELETE", f"/{index}")
        existed = False
        recreated = True

    if existed:
        validate_remote_index(base_url, index, expected=mapping)
        action = "validated_existing"
    else:
        request_json(base_url, "PUT", f"/{index}", body=mapping)
        validate_remote_index(base_url, index, expected=mapping)
        action = "created"

    return {
        "action": action,
        "index": index,
        "recreated": recreated,
        "document_count": get_document_count(base_url, index),
        "remote": get_remote_summary(base_url, index),
    }


def get_remote_summary(base_url: str, index: str) -> dict[str, Any]:
    settings_response = request_json(base_url, "GET", f"/{index}/_settings")
    mappings_response = request_json(base_url, "GET", f"/{index}/_mapping")
    index_settings = settings_response[index]["settings"]["index"]
    return {
        "number_of_shards": int(index_settings["number_of_shards"]),
        "number_of_replicas": int(index_settings["number_of_replicas"]),
        "dynamic": mappings_response[index]["mappings"].get("dynamic"),
        "fields": sorted(mappings_response[index]["mappings"].get("properties", {}).keys()),
    }


def get_document_count(base_url: str, index: str) -> int:
    response = request_json(base_url, "GET", f"/{index}/_count")
    return int(response.get("count", -1))


def validate_remote_index(base_url: str, index: str, expected: dict[str, Any]) -> None:
    settings_response = request_json(base_url, "GET", f"/{index}/_settings")
    mappings_response = request_json(base_url, "GET", f"/{index}/_mapping")

    index_settings = settings_response[index]["settings"]["index"]
    expected_settings = expected["settings"]["index"]
    if int(index_settings["number_of_shards"]) != expected_settings["number_of_shards"]:
        raise ElasticsearchError("remote index has unexpected number_of_shards")
    if int(index_settings["number_of_replicas"]) != expected_settings["number_of_replicas"]:
        raise ElasticsearchError("remote index has unexpected number_of_replicas")

    remote_mapping = mappings_response[index]["mappings"]
    if remote_mapping.get("dynamic") != expected["mappings"]["dynamic"]:
        raise ElasticsearchError("remote index has unexpected dynamic setting")

    remote_properties = remote_mapping.get("properties", {})
    expected_properties = expected["mappings"]["properties"]
    if set(remote_properties) != set(expected_properties):
        missing = sorted(set(expected_properties) - set(remote_properties))
        extra = sorted(set(remote_properties) - set(expected_properties))
        raise ElasticsearchError(f"remote index fields mismatch; missing={missing}, extra={extra}")

    for field, expected_field in expected_properties.items():
        remote_field = remote_properties[field]
        for key, expected_value in expected_field.items():
            if remote_field.get(key) != expected_value:
                raise ElasticsearchError(
                    f"remote field {field!r} has {key}={remote_field.get(key)!r}; "
                    f"expected {expected_value!r}"
                )
        if "fields" in remote_field:
            raise ElasticsearchError(f"remote field {field!r} unexpectedly defines multi-fields")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("ELASTICSEARCH_URL", DEFAULT_ES_URL))
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--mapping", type=Path, default=Path(DEFAULT_MAPPING))
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and recreate the target index if it already exists.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        mapping = read_mapping(args.mapping)
        result = create_or_validate_index(
            base_url=args.host,
            index=args.index,
            mapping=mapping,
            recreate=args.recreate,
        )
        result.update(
            {
                "status": "passed",
                "host": args.host,
                "mapping_file": str(args.mapping),
                "mapping_sha256": sha256_file(args.mapping),
            }
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except ElasticsearchError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
