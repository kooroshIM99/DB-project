#!/usr/bin/env python3
"""Ingest the fixed 50k dataset into the separate stage-9 optimized index."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from scripts import index_dataset
except ModuleNotFoundError:
    import index_dataset  # type: ignore[no-redef]


OPTIMIZED_INDEX = "arxiv_papers_optimized"
DEFAULT_REPORT = Path("results/ingestion_optimized.json")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default=os.environ.get("ELASTICSEARCH_URL", index_dataset.DEFAULT_ES_URL)
    )
    parser.add_argument("--dataset", type=Path, default=index_dataset.DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--batch-size", type=int, default=index_dataset.DEFAULT_BATCH_SIZE)
    parser.add_argument("--expected-count", type=int, default=index_dataset.EXPECTED_DOCUMENT_COUNT)
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = index_dataset.ingest(
            base_url=args.host,
            dataset=args.dataset,
            report_path=args.report,
            batch_size=args.batch_size,
            expected_count=args.expected_count,
            timeout=args.timeout,
            index=OPTIMIZED_INDEX,
            benchmark_type="optimized_ingestion",
        )
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "passed" else 1
    except (index_dataset.IngestionError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
