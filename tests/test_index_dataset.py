from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import index_dataset


def write_jsonl(path: Path, documents: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(document) + "\n" for document in documents), encoding="utf-8"
    )


def test_read_documents_streams_valid_objects(tmp_path: Path) -> None:
    dataset = tmp_path / "sample.jsonl"
    documents = [{"paper_id": "one", "title": "A"}, {"paper_id": "two", "title": "B"}]
    write_jsonl(dataset, documents)

    assert list(index_dataset.read_documents(dataset)) == [(1, documents[0]), (2, documents[1])]


@pytest.mark.parametrize("value", [[], {"title": "missing id"}, {"paper_id": ""}])
def test_read_documents_rejects_invalid_records(tmp_path: Path, value: object) -> None:
    dataset = tmp_path / "invalid.jsonl"
    dataset.write_text(json.dumps(value) + "\n", encoding="utf-8")

    with pytest.raises(index_dataset.IngestionError):
        list(index_dataset.read_documents(dataset))


def test_batched_preserves_items_and_last_partial_batch() -> None:
    items = [(number, {"paper_id": str(number)}) for number in range(1, 6)]
    batches = list(index_dataset.batched(iter(items), 2))
    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert [item for batch in batches for item in batch] == items


def test_bulk_body_uses_paper_id_as_id_and_ndjson() -> None:
    document = {"paper_id": "gr-qc/0412088", "title": "Example"}
    body = index_dataset.make_bulk_body([(17, document)])
    lines = body.decode("utf-8").splitlines()

    assert body.endswith(b"\n")
    assert json.loads(lines[0]) == {
        "index": {
            "_index": index_dataset.BASELINE_INDEX,
            "_id": "gr-qc/0412088",
        }
    }
    assert json.loads(lines[1]) == document


def test_bulk_body_can_target_only_an_explicit_stage9_index() -> None:
    document = {"paper_id": "one"}
    action = json.loads(
        index_dataset.make_bulk_body([(1, document)], "arxiv_papers_optimized")
        .decode()
        .splitlines()[0]
    )
    assert action["index"]["_index"] == "arxiv_papers_optimized"


def test_inspect_bulk_response_counts_successes_and_errors() -> None:
    batch = [(1, {"paper_id": "one"}), (2, {"paper_id": "two"})]
    response = {
        "errors": True,
        "items": [
            {"index": {"status": 201, "result": "created"}},
            {"index": {"status": 400, "error": {"type": "mapper_parsing_exception"}}},
        ],
    }

    succeeded, created, updated, errors = index_dataset.inspect_bulk_response(response, batch)
    assert (succeeded, created, updated) == (1, 1, 0)
    assert errors == [
        {
            "line": 2,
            "paper_id": "two",
            "status": 400,
            "error": {"type": "mapper_parsing_exception"},
        }
    ]


def test_defaults_are_fixed_to_stage_4_contract() -> None:
    args = index_dataset.parse_args([])
    assert index_dataset.BASELINE_INDEX == "arxiv_papers_baseline"
    assert args.dataset == Path("dataset/arxiv_project_sample_50k_cleaned.jsonl")
    assert args.report == Path("results/ingestion_baseline.json")
    assert args.batch_size == 500
    assert args.expected_count == 50_000
