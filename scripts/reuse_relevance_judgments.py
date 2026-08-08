#!/usr/bin/env python3
"""Reuse manual judgments when optimized rankings contain previously judged papers."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("results/relevance_judgments.csv"))
    parser.add_argument("--template", type=Path, default=Path("results/relevance_judgments_optimized_template.csv"))
    parser.add_argument("--output", type=Path, default=Path("results/relevance_judgments_optimized.csv"))
    args = parser.parse_args()
    with args.source.open(encoding="utf-8", newline="") as file:
        source = list(csv.DictReader(file))
    judgments: dict[tuple[str, str], str] = {}
    for row in source:
        key = (row["query_id"], row["paper_id"])
        previous = judgments.setdefault(key, row["judgment"])
        if previous != row["judgment"]:
            raise ValueError(f"inconsistent prior judgment for {key}")
    with args.template.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        rows = list(reader)
    missing = []
    for row in rows:
        key = (row["query_id"], row["paper_id"])
        if key not in judgments:
            missing.append(key)
        else:
            row["judgment"] = judgments[key]
            row["notes"] = "Reused prior manual judgment for identical query and paper."
    if missing:
        raise ValueError(f"{len(missing)} optimized query/paper pairs require manual judgment")
    assert fieldnames is not None
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(args.output)
    print(f"reused {len(rows)} complete judgments -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
