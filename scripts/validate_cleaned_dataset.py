#!/usr/bin/env python3
"""Independently validate the canonical cleaned arXiv JSONL dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


EXPECTED_FIELDS = (
    "paper_id",
    "title",
    "abstract",
    "title_abstract",
    "authors",
    "categories",
    "primary_category",
    "year",
    "update_date",
)
TEXT_FIELDS = ("paper_id", "title", "abstract", "title_abstract", "authors", "primary_category", "update_date")
MODERN_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
LEGACY_ARXIV_ID = re.compile(r"^[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?$")
CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WHITESPACE_PATTERN = re.compile(r"\s+")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def distribution(values: list[int]) -> dict[str, float | int]:
    return {
        "min": min(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "mean": round(statistics.fmean(values), 2),
    }


def validate_dataset(path: Path) -> tuple[dict[str, Any], int]:
    errors: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    record_count = 0
    ids: set[str] = set()
    titles: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    id_formats: Counter[str] = Counter()
    dates: list[date] = []
    lengths: dict[str, list[int]] = {field_name: [] for field_name in ("title", "abstract", "title_abstract", "authors")}
    category_counts_per_record: list[int] = []

    def add_error(code: str, line_number: int, detail: str) -> None:
        errors[code] += 1
        if len(examples[code]) < 5:
            examples[code].append({"line": line_number, "detail": detail})

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            record_count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                add_error("invalid_json", line_number, str(exc))
                continue
            if not isinstance(record, dict):
                add_error("non_object_json", line_number, type(record).__name__)
                continue
            if tuple(record.keys()) != EXPECTED_FIELDS:
                add_error("invalid_field_set_or_order", line_number, repr(tuple(record.keys())))
                continue

            for field_name in TEXT_FIELDS:
                value = record[field_name]
                if not isinstance(value, str):
                    add_error("invalid_text_type", line_number, f"{field_name}: {type(value).__name__}")
                    continue
                if value != unicodedata.normalize("NFC", value):
                    add_error("non_nfc_text", line_number, field_name)
                if value != WHITESPACE_PATTERN.sub(" ", value).strip():
                    add_error("non_canonical_whitespace", line_number, field_name)
                if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
                    add_error("control_or_format_character", line_number, field_name)

            paper_id = record["paper_id"]
            if isinstance(paper_id, str):
                if MODERN_ARXIV_ID.fullmatch(paper_id):
                    id_formats["modern"] += 1
                elif LEGACY_ARXIV_ID.fullmatch(paper_id):
                    id_formats["legacy"] += 1
                else:
                    add_error("invalid_paper_id", line_number, paper_id)
                if paper_id in ids:
                    add_error("duplicate_paper_id", line_number, paper_id)
                ids.add(paper_id)

            for required_non_empty in ("paper_id", "title", "abstract", "update_date"):
                value = record[required_non_empty]
                if not isinstance(value, str) or not value:
                    add_error("empty_required_field", line_number, required_non_empty)

            authors = record["authors"]
            if not isinstance(authors, str):
                add_error("invalid_authors_type", line_number, type(authors).__name__)

            category_list = record["categories"]
            if not isinstance(category_list, list) or not category_list:
                add_error("invalid_categories", line_number, type(category_list).__name__)
            else:
                category_counts_per_record.append(len(category_list))
                if len(category_list) != len(set(category_list)):
                    add_error("duplicate_category", line_number, repr(category_list))
                for category in category_list:
                    if not isinstance(category, str) or not CATEGORY_PATTERN.fullmatch(category):
                        add_error("invalid_category_value", line_number, repr(category))
                    else:
                        categories[category] += 1
                if record["primary_category"] not in category_list:
                    add_error("primary_category_not_in_categories", line_number, record["primary_category"])

            update_date = record["update_date"]
            parsed_date: date | None = None
            if not isinstance(update_date, str) or not DATE_PATTERN.fullmatch(update_date):
                add_error("invalid_update_date", line_number, repr(update_date))
            else:
                try:
                    parsed_date = datetime.strptime(update_date, "%Y-%m-%d").date()
                    dates.append(parsed_date)
                except ValueError:
                    add_error("invalid_update_date", line_number, update_date)

            if type(record["year"]) is not int:
                add_error("invalid_year_type", line_number, type(record["year"]).__name__)
            elif parsed_date is not None and record["year"] != parsed_date.year:
                add_error("year_date_mismatch", line_number, f"{record['year']} vs {update_date}")

            title = record["title"]
            abstract = record["abstract"]
            combined = record["title_abstract"]
            if all(isinstance(value, str) for value in (title, abstract, combined)):
                if combined != f"{title} {abstract}":
                    add_error("title_abstract_mismatch", line_number, str(record.get("paper_id")))
                titles[title] += 1
                lengths["title"].append(len(title))
                lengths["abstract"].append(len(abstract))
                lengths["title_abstract"].append(len(combined))
                lengths["authors"].append(len(authors))

    summary: dict[str, Any] = {
        "status": "passed" if not errors else "failed",
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
        "record_count": record_count,
        "unique_paper_ids": len(ids),
        "id_formats": dict(sorted(id_formats.items())),
        "date_range": {
            "min": min(dates).isoformat() if dates else None,
            "max": max(dates).isoformat() if dates else None,
        },
        "unique_categories": len(categories),
        "top_categories": categories.most_common(20),
        "category_count_per_record": distribution(category_counts_per_record) if category_counts_per_record else {},
        "text_length_distributions": {
            field_name: distribution(values) for field_name, values in lengths.items() if values
        },
        "duplicate_exact_title_values": sum(1 for count in titles.values() if count > 1),
        "duplicate_exact_title_record_excess": sum(count - 1 for count in titles.values() if count > 1),
        "errors": dict(sorted(errors.items())),
        "error_examples": dict(examples),
    }
    return summary, sum(errors.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Clean JSONL file to validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary, error_count = validate_dataset(args.dataset)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
