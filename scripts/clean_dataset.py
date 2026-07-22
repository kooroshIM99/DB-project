#!/usr/bin/env python3
"""Stream, clean, and canonicalize the arXiv JSONL dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT_FIELDS = (
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
REQUIRED_INPUT_FIELDS = ("paper_id", "title", "abstract", "update_date")
MODERN_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
LEGACY_ARXIV_ID = re.compile(r"^[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?$")
CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass
class CleanOutcome:
    """Result and audit details for one input record."""

    record: dict[str, Any] | None
    drop_reason: str | None = None
    changed: bool = False
    control_characters_eliminated: Counter[str] = field(default_factory=Counter)
    normalized_text_fields: set[str] = field(default_factory=set)
    categories_from_string: bool = False
    invalid_category_values_removed: int = 0
    primary_category_repaired: bool = False
    authors_defaulted: bool = False


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return a SHA-256 digest without loading the whole file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> tuple[str, int, bool]:
    """Apply conservative Unicode/control/whitespace normalization."""
    nfc_value = unicodedata.normalize("NFC", value)
    characters: list[str] = []
    removed_controls = 0

    for character in nfc_value:
        unicode_category = unicodedata.category(character)
        if unicode_category in {"Cc", "Cf"}:
            if character in "\t\n\r":
                characters.append(" ")
            else:
                removed_controls += 1
            continue
        characters.append(character)

    normalized = WHITESPACE_PATTERN.sub(" ", "".join(characters)).strip()
    return normalized, removed_controls, normalized != value


def _clean_categories(value: Any) -> tuple[list[str], bool, int, int, bool]:
    """Return clean categories plus audit counters."""
    from_string = isinstance(value, str)
    if from_string:
        raw_values: list[Any] = value.split()
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = []

    cleaned_values: list[str] = []
    seen: set[str] = set()
    removed_controls = 0
    invalid_removed = 0
    normalized = from_string

    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            invalid_removed += 1
            normalized = True
            continue

        cleaned, control_count, text_changed = normalize_text(raw_value)
        removed_controls += control_count
        normalized = normalized or text_changed
        if not cleaned or not CATEGORY_PATTERN.fullmatch(cleaned):
            invalid_removed += 1
            normalized = True
            continue
        if cleaned in seen:
            normalized = True
            continue
        seen.add(cleaned)
        cleaned_values.append(cleaned)

    return cleaned_values, from_string, invalid_removed, removed_controls, normalized


def _is_valid_arxiv_id(paper_id: str) -> bool:
    return bool(MODERN_ARXIV_ID.fullmatch(paper_id) or LEGACY_ARXIV_ID.fullmatch(paper_id))


def clean_record(record: Any, seen_ids: set[str] | None = None) -> CleanOutcome:
    """Validate and canonicalize one record according to the project contract."""
    if not isinstance(record, dict):
        return CleanOutcome(None, drop_reason="non_object_json")

    if any(field_name not in record for field_name in REQUIRED_INPUT_FIELDS):
        return CleanOutcome(None, drop_reason="missing_required_field")

    outcome = CleanOutcome(record=None)

    # Audit the source derived field even though it is always rebuilt below.
    source_combined = record.get("title_abstract")
    if isinstance(source_combined, str):
        _, controls, changed = normalize_text(source_combined)
        outcome.control_characters_eliminated["title_abstract"] += controls
        if changed:
            outcome.normalized_text_fields.add("title_abstract")

    raw_id = record.get("paper_id")
    if not isinstance(raw_id, str):
        return CleanOutcome(None, drop_reason="invalid_paper_id")
    paper_id, controls, changed = normalize_text(raw_id)
    outcome.control_characters_eliminated["paper_id"] += controls
    if changed:
        outcome.normalized_text_fields.add("paper_id")
    if not paper_id or not _is_valid_arxiv_id(paper_id):
        return CleanOutcome(None, drop_reason="invalid_paper_id")
    if seen_ids is not None and paper_id in seen_ids:
        return CleanOutcome(None, drop_reason="duplicate_paper_id")

    raw_title = record.get("title")
    if not isinstance(raw_title, str):
        return CleanOutcome(None, drop_reason="invalid_title")
    title, controls, changed = normalize_text(raw_title)
    outcome.control_characters_eliminated["title"] += controls
    if changed:
        outcome.normalized_text_fields.add("title")
    if not title:
        return CleanOutcome(None, drop_reason="invalid_title")

    raw_abstract = record.get("abstract")
    if not isinstance(raw_abstract, str):
        return CleanOutcome(None, drop_reason="invalid_abstract")
    abstract, controls, changed = normalize_text(raw_abstract)
    outcome.control_characters_eliminated["abstract"] += controls
    if changed:
        outcome.normalized_text_fields.add("abstract")
    if not abstract:
        return CleanOutcome(None, drop_reason="invalid_abstract")

    raw_authors = record.get("authors", "")
    if not isinstance(raw_authors, str):
        raw_authors = ""
        outcome.authors_defaulted = True
    authors, controls, changed = normalize_text(raw_authors)
    outcome.control_characters_eliminated["authors"] += controls
    if changed:
        outcome.normalized_text_fields.add("authors")

    (
        categories,
        outcome.categories_from_string,
        outcome.invalid_category_values_removed,
        category_controls,
        categories_changed,
    ) = _clean_categories(record.get("categories"))
    outcome.control_characters_eliminated["categories"] += category_controls
    if categories_changed:
        outcome.normalized_text_fields.add("categories")
    if not categories:
        return CleanOutcome(None, drop_reason="invalid_categories")

    raw_primary = record.get("primary_category", "")
    if not isinstance(raw_primary, str):
        raw_primary = ""
    primary_category, controls, changed = normalize_text(raw_primary)
    outcome.control_characters_eliminated["primary_category"] += controls
    if changed:
        outcome.normalized_text_fields.add("primary_category")
    if primary_category not in categories:
        primary_category = categories[0]
        outcome.primary_category_repaired = True

    raw_update_date = record.get("update_date")
    if not isinstance(raw_update_date, str):
        return CleanOutcome(None, drop_reason="invalid_update_date")
    update_date, controls, changed = normalize_text(raw_update_date)
    outcome.control_characters_eliminated["update_date"] += controls
    if changed:
        outcome.normalized_text_fields.add("update_date")
    if not DATE_PATTERN.fullmatch(update_date):
        return CleanOutcome(None, drop_reason="invalid_update_date")
    try:
        parsed_date = datetime.strptime(update_date, "%Y-%m-%d")
    except ValueError:
        return CleanOutcome(None, drop_reason="invalid_update_date")

    cleaned_record = {
        "paper_id": paper_id,
        "title": title,
        "abstract": abstract,
        "title_abstract": f"{title} {abstract}",
        "authors": authors,
        "categories": categories,
        "primary_category": primary_category,
        "year": parsed_date.year,
        "update_date": update_date,
    }
    original_canonical = {field_name: record.get(field_name) for field_name in OUTPUT_FIELDS}
    outcome.record = cleaned_record
    outcome.changed = cleaned_record != original_canonical

    if seen_ids is not None:
        seen_ids.add(paper_id)
    return outcome


def clean_dataset(input_path: Path, output_path: Path, report_path: Path) -> dict[str, Any]:
    """Clean a JSONL file, atomically publish output, and write an audit report."""
    started_at = time.perf_counter()
    input_size = input_path.stat().st_size
    input_hash = file_sha256(input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_name(f".{output_path.name}.tmp")

    input_records = 0
    output_records = 0
    changed_records = 0
    dropped_by_reason: Counter[str] = Counter()
    controls_by_field: Counter[str] = Counter()
    normalized_fields: Counter[str] = Counter()
    categories_from_string = 0
    invalid_category_values_removed = 0
    primary_category_repaired = 0
    authors_defaulted = 0
    seen_ids: set[str] = set()

    try:
        with input_path.open("r", encoding="utf-8") as source, temporary_output.open(
            "w", encoding="utf-8", newline="\n"
        ) as destination:
            for line in source:
                input_records += 1
                try:
                    parsed = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    dropped_by_reason["invalid_json"] += 1
                    continue

                outcome = clean_record(parsed, seen_ids)
                if outcome.record is None:
                    dropped_by_reason[outcome.drop_reason or "unknown"] += 1
                    continue

                destination.write(json.dumps(outcome.record, ensure_ascii=False) + "\n")
                output_records += 1
                changed_records += int(outcome.changed)
                controls_by_field.update(outcome.control_characters_eliminated)
                normalized_fields.update(outcome.normalized_text_fields)
                categories_from_string += int(outcome.categories_from_string)
                invalid_category_values_removed += outcome.invalid_category_values_removed
                primary_category_repaired += int(outcome.primary_category_repaired)
                authors_defaulted += int(outcome.authors_defaulted)

            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_output, output_path)
    except Exception:
        if temporary_output.exists():
            temporary_output.unlink()
        raise

    output_hash = file_sha256(output_path)
    duration_seconds = time.perf_counter() - started_at
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_seconds, 6),
        "files": {
            "input": {
                "path": str(input_path),
                "size_bytes": input_size,
                "sha256": input_hash,
            },
            "output": {
                "path": str(output_path),
                "size_bytes": output_path.stat().st_size,
                "sha256": output_hash,
            },
        },
        "records": {
            "input": input_records,
            "output": output_records,
            "dropped": input_records - output_records,
            "changed": changed_records,
            "unchanged": output_records - changed_records,
            "dropped_by_reason": dict(sorted(dropped_by_reason.items())),
        },
        "repairs": {
            "control_characters_eliminated_by_field": dict(sorted(controls_by_field.items())),
            "records_normalized_by_field": dict(sorted(normalized_fields.items())),
            "categories_converted_from_string": categories_from_string,
            "invalid_category_values_removed": invalid_category_values_removed,
            "primary_category_repaired": primary_category_repaired,
            "authors_defaulted": authors_defaulted,
            "year_rebuilt": output_records,
            "title_abstract_rebuilt": output_records,
        },
        "output_schema": list(OUTPUT_FIELDS),
    }
    temporary_report = report_path.with_name(f".{report_path.name}.tmp")
    with temporary_report.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_report, report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Source JSONL path")
    parser.add_argument("--output", required=True, type=Path, help="Clean JSONL path")
    parser.add_argument("--report", required=True, type=Path, help="Cleaning report JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = clean_dataset(args.input, args.output, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
