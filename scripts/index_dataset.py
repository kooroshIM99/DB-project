#!/usr/bin/env python3
"""Stream the cleaned arXiv dataset into the stage-4 baseline index.

The script uses Elasticsearch's NDJSON Bulk API. It is intentionally limited
to ``arxiv_papers_baseline`` so stage-4 data cannot leak into the optimized
index that belongs to stage 9.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


DEFAULT_ES_URL = "http://127.0.0.1:9200"
BASELINE_INDEX = "arxiv_papers_baseline"
DEFAULT_DATASET = Path("dataset/arxiv_project_sample_50k_cleaned.jsonl")
DEFAULT_REPORT = Path("results/ingestion_baseline.json")
DEFAULT_BATCH_SIZE = 500
EXPECTED_DOCUMENT_COUNT = 50_000
MAX_RECORDED_ERRORS = 100


class IngestionError(RuntimeError):
    """Raised when input validation or an Elasticsearch request fails."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_documents(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield one validated JSON object at a time without loading the dataset."""
    try:
        file = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise IngestionError(f"cannot open dataset {path}: {exc}") from exc

    with file:
        for line_number, line in enumerate(file, start=1):
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IngestionError(f"invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(document, dict):
                raise IngestionError(f"line {line_number} is not a JSON object")
            paper_id = document.get("paper_id")
            if not isinstance(paper_id, str) or not paper_id:
                raise IngestionError(f"line {line_number} has no valid paper_id")
            yield line_number, document


def batched(
    documents: Iterable[tuple[int, dict[str, Any]]], batch_size: int
) -> Iterator[list[tuple[int, dict[str, Any]]]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    batch: list[tuple[int, dict[str, Any]]] = []
    for item in documents:
        batch.append(item)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def make_bulk_body(batch: list[tuple[int, dict[str, Any]]]) -> bytes:
    lines: list[str] = []
    for _, document in batch:
        action = {"index": {"_index": BASELINE_INDEX, "_id": document["paper_id"]}}
        lines.append(json.dumps(action, ensure_ascii=False, separators=(",", ":")))
        lines.append(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("utf-8")


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    timeout: float = 60.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise IngestionError(f"{method} {path} failed with HTTP {exc.code}: {error_body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IngestionError(f"{method} {path} failed: {exc}") from exc
    try:
        result = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise IngestionError(f"{method} {path} returned invalid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise IngestionError(f"{method} {path} returned a non-object JSON response")
    return result


def require_existing_baseline(base_url: str, timeout: float) -> None:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/{BASELINE_INDEX}", method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise IngestionError(
                f"required index {BASELINE_INDEX!r} does not exist; run scripts/create_index.py first"
            ) from exc
        raise IngestionError(f"HEAD /{BASELINE_INDEX} failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IngestionError(f"HEAD /{BASELINE_INDEX} failed: {exc}") from exc


def inspect_bulk_response(
    response: dict[str, Any], batch: list[tuple[int, dict[str, Any]]]
) -> tuple[int, int, int, list[dict[str, Any]]]:
    items = response.get("items")
    if not isinstance(items, list) or len(items) != len(batch):
        raise IngestionError(
            f"bulk response item count mismatch: expected {len(batch)}, got "
            f"{len(items) if isinstance(items, list) else 'invalid'}"
        )

    succeeded = created = updated = 0
    errors: list[dict[str, Any]] = []
    for (line_number, document), item in zip(batch, items):
        action = item.get("index", {}) if isinstance(item, dict) else {}
        status = action.get("status")
        error = action.get("error")
        if isinstance(status, int) and 200 <= status < 300 and error is None:
            succeeded += 1
            if action.get("result") == "created":
                created += 1
            else:
                updated += 1
        else:
            errors.append(
                {
                    "line": line_number,
                    "paper_id": document["paper_id"],
                    "status": status,
                    "error": error or "unknown bulk item error",
                }
            )
    return succeeded, created, updated, errors


def index_stats(base_url: str, timeout: float) -> dict[str, int]:
    count = request_json(base_url, "GET", f"/{BASELINE_INDEX}/_count", timeout=timeout)
    stats = request_json(
        base_url, "GET", f"/{BASELINE_INDEX}/_stats/store,docs", timeout=timeout
    )
    try:
        index_data = stats["indices"][BASELINE_INDEX]
        return {
            "document_count": int(count["count"]),
            "store_size_bytes": int(index_data["total"]["store"]["size_in_bytes"]),
            "primary_store_size_bytes": int(
                index_data["primaries"]["store"]["size_in_bytes"]
            ),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestionError(f"unexpected index stats response: {exc}") from exc


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def ingest(
    *,
    base_url: str,
    dataset: Path,
    report_path: Path,
    batch_size: int,
    expected_count: int,
    timeout: float,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise IngestionError("batch size must be positive")
    if expected_count <= 0:
        raise IngestionError("expected document count must be positive")
    if not dataset.is_file():
        raise IngestionError(f"dataset file not found: {dataset}")

    dataset_hash = sha256_file(dataset)
    require_existing_baseline(base_url, timeout)
    started_at = utc_now()
    timer = time.perf_counter()
    processed = succeeded = created = updated = error_count = batch_count = 0
    recorded_errors: list[dict[str, Any]] = []

    try:
        for batch in batched(read_documents(dataset), batch_size):
            response = request_json(
                base_url,
                "POST",
                "/_bulk",
                body=make_bulk_body(batch),
                content_type="application/x-ndjson",
                timeout=timeout,
            )
            batch_succeeded, batch_created, batch_updated, batch_errors = inspect_bulk_response(
                response, batch
            )
            batch_count += 1
            processed += len(batch)
            succeeded += batch_succeeded
            created += batch_created
            updated += batch_updated
            error_count += len(batch_errors)
            remaining = MAX_RECORDED_ERRORS - len(recorded_errors)
            if remaining > 0:
                recorded_errors.extend(batch_errors[:remaining])

        request_json(base_url, "POST", f"/{BASELINE_INDEX}/_refresh", timeout=timeout)
        stats = index_stats(base_url, timeout)
        duration = time.perf_counter() - timer
        passed = (
            processed == expected_count
            and succeeded == expected_count
            and error_count == 0
            and stats["document_count"] == expected_count
        )
        report: dict[str, Any] = {
            "status": "passed" if passed else "failed",
            "benchmark_type": "baseline_ingestion",
            "is_search_benchmark": False,
            "index": BASELINE_INDEX,
            "host": base_url,
            "dataset": str(dataset),
            "dataset_sha256": dataset_hash,
            "dataset_size_bytes": dataset.stat().st_size,
            "batch_size": batch_size,
            "batch_count": batch_count,
            "expected_document_count": expected_count,
            "processed_document_count": processed,
            "successful_action_count": succeeded,
            "created_action_count": created,
            "updated_action_count": updated,
            "bulk_error_count": error_count,
            "recorded_errors": recorded_errors,
            "errors_truncated": error_count > len(recorded_errors),
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_seconds": round(duration, 6),
            "documents_per_second": round(succeeded / duration, 3) if duration else None,
            **stats,
        }
        if not passed:
            report["failure_reason"] = (
                "ingestion did not meet the expected action count, zero-error, and final-count contract"
            )
        write_report(report_path, report)
        return report
    except Exception as exc:
        duration = time.perf_counter() - timer
        report = {
            "status": "failed",
            "benchmark_type": "baseline_ingestion",
            "is_search_benchmark": False,
            "index": BASELINE_INDEX,
            "host": base_url,
            "dataset": str(dataset),
            "dataset_sha256": dataset_hash,
            "batch_size": batch_size,
            "processed_document_count": processed,
            "successful_action_count": succeeded,
            "bulk_error_count": error_count,
            "recorded_errors": recorded_errors,
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_seconds": round(duration, 6),
            "failure_reason": str(exc),
        }
        write_report(report_path, report)
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("ELASTICSEARCH_URL", DEFAULT_ES_URL))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--expected-count", type=int, default=EXPECTED_DOCUMENT_COUNT)
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = ingest(
            base_url=args.host,
            dataset=args.dataset,
            report_path=args.report,
            batch_size=args.batch_size,
            expected_count=args.expected_count,
            timeout=args.timeout,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "passed" else 1
    except (IngestionError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
