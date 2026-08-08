from __future__ import annotations

import pytest

from scripts.search_ui import build_search_body


def test_keyword_query_uses_track_total_hits_and_filters() -> None:
    body = build_search_body({
        "q": "query optimization",
        "mode": "keyword",
        "category": "cs.DB",
        "year": "2024",
        "size": "15",
    })
    assert body["track_total_hits"] is True
    assert body["size"] == 15
    bool_query = body["query"]["bool"]
    assert {"term": {"categories": "cs.DB"}} in bool_query["filter"]
    assert {"term": {"year": 2024}} in bool_query["filter"]


def test_fuzzy_query_sets_fuzziness() -> None:
    body = build_search_body({"q": "databse optimiztion", "mode": "fuzzy"})
    multi_match = body["query"]["bool"]["must"][0]["multi_match"]
    assert multi_match["fuzziness"] == "AUTO"
    assert multi_match["prefix_length"] == 1


def test_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError, match="Search text is required"):
        build_search_body({"q": "   "})
