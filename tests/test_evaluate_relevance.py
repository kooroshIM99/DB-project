from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts import evaluate_relevance


def sample_report() -> dict:
    scenarios = []
    for query_id in ("q1", "q2", "q3"):
        for method in ("keyword", "contain", "fuzzy", "hybrid"):
            count = 1 if query_id != "q3" else 0
            scenarios.append(
                {
                    "query_id": query_id,
                    "user_input": query_id,
                    "method": method,
                    "result_count": count,
                    "top_hits": (
                        [{"rank": 1, "paper_id": f"{query_id}-{method}", "title": "Title"}]
                        if count
                        else []
                    ),
                }
            )
    return {"status": "passed", "scenarios": scenarios}


def write_judgments(path: Path, expected: dict, *, blank: bool = False) -> None:
    fields = ["query", "query_id", "method", "rank", "paper_id", "title", "judgment", "notes"]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for (query_id, method, rank), value in expected.items():
            writer.writerow(
                {
                    "query": value["query"],
                    "query_id": query_id,
                    "method": method,
                    "rank": rank,
                    "paper_id": value["paper_id"],
                    "title": value["title"],
                    "judgment": "" if blank else "relevant",
                    "notes": "manual review",
                }
            )


def test_quality_penalizes_missing_ranks() -> None:
    report = sample_report()
    expected = evaluate_relevance.expected_rows(report)
    judgments = {
        key: {"judgment": "relevant"} for key in expected
    }
    quality = evaluate_relevance.calculate_quality(report, judgments)

    populated = next(row for row in quality if row["query_id"] == "q1")
    empty = next(row for row in quality if row["query_id"] == "q3")
    assert populated["precision_at_10"] == 0.1
    assert populated["precision_at_returned"] == 1.0
    assert populated["missing_ranks"] == 9
    assert empty["precision_at_10"] == 0.0
    assert empty["precision_at_returned"] is None
    assert empty["missing_ranks"] == 10


def test_judgment_loader_rejects_blank_values(tmp_path: Path) -> None:
    report = sample_report()
    expected = evaluate_relevance.expected_rows(report)
    path = tmp_path / "judgments.csv"
    write_judgments(path, expected, blank=True)

    with pytest.raises(evaluate_relevance.RelevanceError, match="relevant or not_relevant"):
        evaluate_relevance.load_judgments(path, expected)


def test_judgment_loader_accepts_exact_manual_set(tmp_path: Path) -> None:
    report = sample_report()
    expected = evaluate_relevance.expected_rows(report)
    path = tmp_path / "judgments.csv"
    write_judgments(path, expected)

    loaded = evaluate_relevance.load_judgments(path, expected)
    assert len(loaded) == 8
