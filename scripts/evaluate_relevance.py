#!/usr/bin/env python3
"""Validate manual stage-6 judgments and calculate Precision@10 quality tables."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark
except ModuleNotFoundError:  # Direct execution from scripts/.
    import benchmark  # type: ignore[no-redef]


DEFAULT_COMPARISON = Path("results/hybrid_comparison.json")
DEFAULT_JUDGMENTS = Path("results/relevance_judgments.csv")
DEFAULT_QUALITY_CSV = Path("results/hybrid_quality.csv")
DEFAULT_QUALITY_JSON = Path("results/hybrid_quality.json")
VALID_JUDGMENTS = {"relevant", "not_relevant"}


class RelevanceError(RuntimeError):
    """Raised when manual judgments are incomplete or inconsistent."""


def load_comparison(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RelevanceError(f"cannot read comparison report {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RelevanceError(f"invalid comparison JSON in {path}: {exc}") from exc
    if not isinstance(report, dict) or report.get("status") != "passed":
        raise RelevanceError("comparison report is missing or did not pass")
    if not isinstance(report.get("scenarios"), list) or len(report["scenarios"]) != 12:
        raise RelevanceError("comparison report must contain 12 method/query scenarios")
    return report


def expected_rows(report: dict[str, Any]) -> dict[tuple[str, str, int], dict[str, Any]]:
    expected: dict[tuple[str, str, int], dict[str, Any]] = {}
    for scenario in report["scenarios"]:
        for hit in scenario["top_hits"]:
            key = (scenario["query_id"], scenario["method"], int(hit["rank"]))
            if key in expected:
                raise RelevanceError(f"duplicate expected judgment key {key}")
            expected[key] = {
                "paper_id": hit["paper_id"],
                "title": hit["title"],
                "query": scenario["user_input"],
            }
    return expected


def load_judgments(
    path: Path, expected: dict[tuple[str, str, int], dict[str, Any]]
) -> dict[tuple[str, str, int], dict[str, str]]:
    try:
        file = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise RelevanceError(f"cannot read judgments {path}: {exc}") from exc
    rows: dict[tuple[str, str, int], dict[str, str]] = {}
    with file:
        reader = csv.DictReader(file)
        required = {"query", "query_id", "method", "rank", "paper_id", "title", "judgment", "notes"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise RelevanceError("judgment CSV is missing required columns")
        for line_number, row in enumerate(reader, start=2):
            try:
                key = (row["query_id"], row["method"], int(row["rank"]))
            except (TypeError, ValueError) as exc:
                raise RelevanceError(f"invalid judgment key at line {line_number}") from exc
            if key in rows:
                raise RelevanceError(f"duplicate judgment key {key}")
            if key not in expected:
                raise RelevanceError(f"unexpected judgment key {key}")
            if row["paper_id"] != expected[key]["paper_id"]:
                raise RelevanceError(f"paper_id mismatch for judgment key {key}")
            if row["judgment"] not in VALID_JUDGMENTS:
                raise RelevanceError(
                    f"judgment for {key} must be relevant or not_relevant"
                )
            rows[key] = row
    missing = set(expected) - set(rows)
    if missing:
        raise RelevanceError(f"manual judgments are incomplete: {len(missing)} rows missing")
    return rows


def calculate_quality(
    report: dict[str, Any], judgments: dict[tuple[str, str, int], dict[str, str]]
) -> list[dict[str, Any]]:
    quality: list[dict[str, Any]] = []
    for scenario in sorted(report["scenarios"], key=lambda item: (item["query_id"], item["method"])):
        scenario_rows = [
            row
            for (query_id, method, _), row in judgments.items()
            if query_id == scenario["query_id"] and method == scenario["method"]
        ]
        returned_count = len(scenario["top_hits"])
        judged_count = len(scenario_rows)
        relevant_count = sum(row["judgment"] == "relevant" for row in scenario_rows)
        if judged_count != returned_count:
            raise RelevanceError(
                f"judged/returned mismatch for {scenario['query_id']}/{scenario['method']}"
            )
        quality.append(
            {
                "query_id": scenario["query_id"],
                "query": scenario["user_input"],
                "method": scenario["method"],
                "total_result_count": scenario["result_count"],
                "returned_count": returned_count,
                "judged_count": judged_count,
                "missing_ranks": 10 - returned_count,
                "relevant_count": relevant_count,
                "precision_at_10": round(relevant_count / 10, 6),
                "precision_at_returned": (
                    round(relevant_count / returned_count, 6) if returned_count else None
                ),
            }
        )
    return quality


def write_quality_csv(path: Path, quality: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(quality[0])
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(quality)
    temporary.replace(path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--judgments", type=Path, default=DEFAULT_JUDGMENTS)
    parser.add_argument("--quality-csv", type=Path, default=DEFAULT_QUALITY_CSV)
    parser.add_argument("--quality-json", type=Path, default=DEFAULT_QUALITY_JSON)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        report = load_comparison(args.comparison)
        expected = expected_rows(report)
        judgments = load_judgments(args.judgments, expected)
        quality = calculate_quality(report, judgments)
        write_quality_csv(args.quality_csv, quality)
        output = {
            "status": "passed",
            "evaluation_type": "manual_binary_relevance_stage6",
            "comparison_path": str(args.comparison),
            "comparison_sha256": benchmark.sha256_file(args.comparison),
            "judgments_path": str(args.judgments),
            "judgments_sha256": benchmark.sha256_file(args.judgments),
            "judgment_count": len(judgments),
            "relevance_values": sorted(VALID_JUDGMENTS),
            "precision_at_10_denominator": 10,
            "empty_ranks_penalized": True,
            "quality": quality,
        }
        benchmark.atomic_write_json(args.quality_json, output)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "judgment_count": len(judgments),
                    "quality_csv": str(args.quality_csv),
                    "quality_json": str(args.quality_json),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (RelevanceError, benchmark.BenchmarkError, OSError) as exc:
        print(f"relevance error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
