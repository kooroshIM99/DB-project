from __future__ import annotations

import json
from pathlib import Path

import pytest


MAPPING_PATH = Path("elasticsearch/mappings/arxiv_papers_baseline.json")
OPTIMIZED_MAPPING_PATH = Path("elasticsearch/mappings/arxiv_papers_optimized.json")
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


@pytest.fixture(scope="module")
def mapping() -> dict:
    with MAPPING_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def test_baseline_mapping_file_exists_and_is_json(mapping: dict) -> None:
    assert MAPPING_PATH.exists()
    assert isinstance(mapping, dict)
    assert set(mapping) == {"settings", "mappings"}


def test_baseline_index_settings(mapping: dict) -> None:
    settings = mapping["settings"]["index"]
    assert settings["number_of_shards"] == 1
    assert settings["number_of_replicas"] == 0
    assert "analysis" not in settings
    assert "analysis" not in mapping["settings"]


def test_baseline_mapping_is_strict(mapping: dict) -> None:
    assert mapping["mappings"]["dynamic"] == "strict"


def test_exact_standard_dataset_fields(mapping: dict) -> None:
    properties = mapping["mappings"]["properties"]
    assert set(properties) == set(EXPECTED_FIELDS)


@pytest.mark.parametrize("field,expected", EXPECTED_FIELDS.items())
def test_field_contracts(mapping: dict, field: str, expected: dict) -> None:
    actual = mapping["mappings"]["properties"][field]
    for key, value in expected.items():
        assert actual[key] == value


def test_baseline_has_no_optimized_features(mapping: dict) -> None:
    serialized = json.dumps(mapping, sort_keys=True)
    assert "ngram" not in serialized
    assert "edge_ngram" not in serialized
    for field in mapping["mappings"]["properties"].values():
        assert "fields" not in field


def test_create_index_defaults_are_safe() -> None:
    import scripts.create_index as create_index

    args = create_index.parse_args([])
    assert args.index == "arxiv_papers_baseline"
    assert args.mapping == MAPPING_PATH
    assert args.recreate is False


def test_stage9_mapping_is_separate_strict_and_optimized() -> None:
    from scripts import create_index

    optimized = json.loads(OPTIMIZED_MAPPING_PATH.read_text(encoding="utf-8"))
    create_index.validate_mapping_file(optimized)
    assert optimized["mappings"]["_meta"]["index"] == "arxiv_papers_optimized"
    assert optimized["mappings"]["dynamic"] == "strict"
    assert set(optimized["mappings"]["properties"]) == set(EXPECTED_FIELDS)
    substring = optimized["mappings"]["properties"]["title_abstract"]["fields"]["substring"]
    assert substring["analyzer"] == "substring_index"
    assert optimized["settings"]["index"]["number_of_replicas"] == 0
