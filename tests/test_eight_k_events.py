import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.eight_k_events import extract_reviewed_8k_features


class EightKEventFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source_path = root / "source.htm"
        raw = (b'<html><body>FORM 8-K. Item 2.02 Results of Operations and Financial Condition. '
               b'On October 1, Example Corp issued a press release for its first fiscal quarter results. '
               b'The information is furnished pursuant to Item 2.02. Item 5.02 Departure of Directors or Certain Officers; '
               b'Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain Officers. '
               b'Item 5.02(c). The board appointed Jane Doe as CFO. Item 9.01 Financial Statements and Exhibits.</body></html>')
        self.source_path.write_bytes(raw)
        self.run = root / "index"
        self.run.mkdir()
        digest = hashlib.sha256(raw).hexdigest()
        self.normalized_path = self.run / "normalized.json"
        nraw = json.dumps({"issuer_cik": "0000000001", "accession_number": "0000000001-26-000001",
                           "form": "8-K", "filing_date": "2026-10-01",
                           "historical_available_after_utc": "2026-10-01T12:01:00Z",
                           "historical_research_eligible": True, "source_sha256": digest}).encode()
        self.normalized_path.write_bytes(nraw)
        (self.run / "manifest.json").write_text(json.dumps({"output": {"sha256": hashlib.sha256(nraw).hexdigest()},
            "source": {"path": str(self.source_path), "sha256": digest}}), encoding="utf-8")
        self.review_path = root / "review.csv"
        with self.review_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["accession_number", "item", "manual_review_status"])
            w.writeheader()
            w.writerows([{"accession_number": "0000000001-26-000001", "item": item,
                          "manual_review_status": "CONFIRMED"} for item in ("2.02", "5.02", "9.01")])

    def tearDown(self):
        self.tmp.cleanup()

    def test_extracts_reviewed_sections_with_point_in_time_provenance(self):
        out = Path(self.tmp.name) / "out"
        summary = extract_reviewed_8k_features(self.normalized_path, self.review_path, out)
        self.assertEqual(summary["event_count"], 2)
        rows = [json.loads(line) for line in (out / "8k_section_event_features.jsonl").read_text().splitlines()]
        by_item = {row["item"]: row for row in rows}
        self.assertTrue(by_item["2.02"]["press_release_language_present"])
        self.assertIn("first fiscal quarter", by_item["2.02"]["fiscal_period_mentions"])
        self.assertTrue(by_item["5.02"]["appointment_language_present"])
        self.assertEqual(by_item["5.02"]["subitems_observed"], "c")
        self.assertEqual(by_item["5.02"]["event_available_at_utc"], "2026-10-01T12:01:00Z")
        self.assertNotIn("9.01", by_item)

    def test_pending_review_fails_closed(self):
        data = self.review_path.read_text().replace("CONFIRMED", "PENDING")
        self.review_path.write_text(data)
        with self.assertRaisesRegex(ValueError, "manually reviewed"):
            extract_reviewed_8k_features(self.normalized_path, self.review_path, Path(self.tmp.name) / "out")

    def test_modified_source_fails_hash_validation(self):
        self.source_path.write_bytes(self.source_path.read_bytes() + b" changed")
        with self.assertRaisesRegex(ValueError, "Source hash"):
            extract_reviewed_8k_features(self.normalized_path, self.review_path, Path(self.tmp.name) / "out")


if __name__ == "__main__":
    unittest.main()
