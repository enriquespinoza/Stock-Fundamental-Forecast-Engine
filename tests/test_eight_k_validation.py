import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.eight_k_validation import validate_8k_run


class EightKValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.raw_path = root / "source.htm"
        raw = (b"<html><title>FORM 8-K</title><body>Item 2.02 Results of Operations and "
               b"Financial Condition. Furnished pursuant to Item 2.02. Exhibit 99.1. "
               b"Item 5.02 Appointment of Certain Officers. Item 5.02(c). "
               b"Item 9.01 Financial Statements and Exhibits.</body></html>")
        self.raw_path.write_bytes(raw)
        self.run_dir = root / "normalized" / "run"
        self.run_dir.mkdir(parents=True)
        normalized = {
            "schema_version": "8k-item-index-v1", "issuer_cik": "0000000001",
            "accession_number": "0000000001-26-000001", "form": "8-K",
            "filing_date": "2026-01-01", "historical_available_after_utc": "2026-01-01T12:01:00Z",
            "historical_research_eligible": True,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "items": [
                {"item": "2.02", "mention_count": 2},
                {"item": "5.02", "mention_count": 2},
                {"item": "9.01", "mention_count": 1},
            ],
        }
        self.normalized_path = self.run_dir / "normalized.json"
        normalized_raw = json.dumps(normalized).encode()
        self.normalized_path.write_bytes(normalized_raw)
        (self.run_dir / "manifest.json").write_text(json.dumps({
            "output": {"sha256": hashlib.sha256(normalized_raw).hexdigest()},
            "source": {"path": str(self.raw_path), "sha256": hashlib.sha256(raw).hexdigest()},
        }), encoding="utf-8")
        self.out = Path(self.tmp.name) / "validation"

    def tearDown(self):
        self.tmp.cleanup()

    def test_recounts_visible_references_and_emits_conservative_features(self):
        result = validate_8k_run(self.normalized_path, self.out, built_at_utc="2026-01-02T00:00:00Z")
        self.assertTrue(result["item_reference_counts_match_source"])
        features = json.loads((self.out / "filing_features.json").read_text(encoding="utf-8"))
        self.assertTrue(features["item_2_02_present"])
        self.assertTrue(features["item_2_02_results_title_present"])
        self.assertTrue(features["item_2_02_furnished_language_present"])
        self.assertTrue(features["exhibit_99_1_reference_present"])
        self.assertEqual(features["item_5_02_subitems_observed"], "c")
        self.assertTrue(features["item_9_01_exhibit_marker_present"])
        self.assertEqual(result["manual_review_status"], "PENDING")

    def test_source_tampering_fails_closed(self):
        self.raw_path.write_bytes(self.raw_path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "source hash"):
            validate_8k_run(self.normalized_path, self.out, built_at_utc="2026-01-02T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
