import unittest

from scripts.clean_dataset import clean_record, normalize_text


def valid_record(**overrides):
    record = {
        "paper_id": "2501.01046",
        "title": "Efficient Query Processing",
        "abstract": "This paper studies database optimization.",
        "title_abstract": "stale value",
        "authors": "A. Smith, B. Lee",
        "categories": ["cs.DB", "cs.IR"],
        "primary_category": "cs.DB",
        "year": 1900,
        "update_date": "2025-01-03",
    }
    record.update(overrides)
    return record


class NormalizeTextTests(unittest.TestCase):
    def test_control_characters_and_whitespace_are_normalized(self):
        cleaned, removed, changed = normalize_text("  Peter G\x7f\\\"ardenfors\n\tworks  ")
        self.assertEqual(cleaned, 'Peter G\\"ardenfors works')
        self.assertEqual(removed, 1)
        self.assertTrue(changed)

    def test_scientific_punctuation_latex_and_url_are_preserved(self):
        text = r"158$\times$ with \texttt{NAG}; see https://example.org/a?q=1."
        cleaned, removed, changed = normalize_text(text)
        self.assertEqual(cleaned, text)
        self.assertEqual(removed, 0)
        self.assertFalse(changed)


class CleanRecordTests(unittest.TestCase):
    def test_derived_fields_are_rebuilt(self):
        outcome = clean_record(valid_record())
        self.assertIsNotNone(outcome.record)
        self.assertEqual(outcome.record["year"], 2025)
        self.assertEqual(
            outcome.record["title_abstract"],
            "Efficient Query Processing This paper studies database optimization.",
        )
        self.assertTrue(outcome.changed)

    def test_legacy_arxiv_id_is_accepted(self):
        outcome = clean_record(valid_record(paper_id="gr-qc/0412088"))
        self.assertIsNotNone(outcome.record)
        self.assertEqual(outcome.record["paper_id"], "gr-qc/0412088")

    def test_categories_string_is_converted_deduplicated_and_primary_repaired(self):
        outcome = clean_record(
            valid_record(categories="cs.DB cs.IR cs.DB", primary_category="cs.AI")
        )
        self.assertEqual(outcome.record["categories"], ["cs.DB", "cs.IR"])
        self.assertEqual(outcome.record["primary_category"], "cs.DB")
        self.assertTrue(outcome.categories_from_string)
        self.assertTrue(outcome.primary_category_repaired)

    def test_empty_title_or_abstract_is_dropped(self):
        for field_name in ("title", "abstract"):
            with self.subTest(field_name=field_name):
                outcome = clean_record(valid_record(**{field_name: " \n\t "}))
                self.assertIsNone(outcome.record)
                self.assertEqual(outcome.drop_reason, f"invalid_{field_name}")

    def test_invalid_date_is_dropped(self):
        outcome = clean_record(valid_record(update_date="2025-02-30"))
        self.assertIsNone(outcome.record)
        self.assertEqual(outcome.drop_reason, "invalid_update_date")

    def test_duplicate_paper_id_keeps_only_first(self):
        seen_ids = set()
        first = clean_record(valid_record(), seen_ids)
        second = clean_record(valid_record(), seen_ids)
        self.assertIsNotNone(first.record)
        self.assertIsNone(second.record)
        self.assertEqual(second.drop_reason, "duplicate_paper_id")

    def test_non_object_and_missing_required_fields_are_dropped(self):
        self.assertEqual(clean_record([]).drop_reason, "non_object_json")
        self.assertEqual(clean_record({"paper_id": "2501.01046"}).drop_reason, "missing_required_field")

    def test_invalid_category_values_are_removed_when_valid_values_remain(self):
        outcome = clean_record(valid_record(categories=["cs.DB", 12, "bad value", "cs.DB"]))
        self.assertEqual(outcome.record["categories"], ["cs.DB"])
        self.assertEqual(outcome.invalid_category_values_removed, 2)


if __name__ == "__main__":
    unittest.main()
