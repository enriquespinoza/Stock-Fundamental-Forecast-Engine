import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from stock_analyzer.financial_fact_validation import validate_financial_fact_sample


class FinancialFactValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw_path = self.root / "companyfacts.json"
        self.normalized_path = self.root / "normalized.json"
        self.sample_path = self.root / "sample.csv"
        self.output_path = self.root / "audit.csv"
        self.fact = {
            "accn": "0000000001-24-000001", "form": "10-Q",
            "filed": "2024-05-01", "fy": 2024, "fp": "Q1",
            "start": "2024-01-01", "end": "2024-03-31", "val": 123456,
        }
        self.normalized_fact = {
            "accession_number": self.fact["accn"], "form": "10-Q",
            "filed_date": "2024-05-01", "fiscal_year": 2024,
            "fiscal_period": "Q1", "metric": "revenue", "tag": "RevenueTag",
            "unit": "USD", "period_start": "2024-01-01",
            "period_end": "2024-03-31", "value": "123456",
        }
        self.raw_path.write_text(json.dumps({"facts": {"us-gaap": {
            "RevenueTag": {"units": {"USD": [self.fact]}}
        }}}), encoding="utf-8")
        self.normalized_path.write_text(json.dumps({"facts": [self.normalized_fact]}), encoding="utf-8")
        self.row = {
            "issuer_cik": "0000000001", "accession_number": self.fact["accn"],
            "form": "10-Q", "filing_date": "2024-05-01", "metric": "revenue",
            "tag": "RevenueTag", "tag_preference": "0", "value": "123456",
            "unit": "USD", "period_kind": "quarter", "period_start": "2024-01-01",
            "period_end": "2024-03-31", "fiscal_year": "2024", "fiscal_period": "Q1",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1/000000000124000001/report.htm",
            "companyfacts_snapshot_path": str(self.raw_path),
            "companyfacts_snapshot_sha256": hashlib.sha256(self.raw_path.read_bytes()).hexdigest(),
            "normalized_financials_path": str(self.normalized_path),
            "normalized_financials_sha256": hashlib.sha256(self.normalized_path.read_bytes()).hexdigest(),
            "manual_review_status": "PENDING", "observed_value": "", "review_notes": "",
        }
        with self.sample_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.row, lineterminator="\n")
            writer.writeheader()
            writer.writerow(self.row)

    def tearDown(self):
        self.temp.cleanup()

    def read_output(self):
        with self.output_path.open(encoding="utf-8", newline="") as stream:
            return next(csv.DictReader(stream))

    def test_exact_companyfacts_and_normalized_match_is_not_manual_confirmation(self):
        summary = validate_financial_fact_sample(self.sample_path, self.output_path)
        row = self.read_output()
        self.assertEqual(summary["input_rows"], 1)
        self.assertEqual(row["automated_validation_status"], "MATCHES_COMPANYFACTS_AND_NORMALIZED_OUTPUT")
        self.assertEqual(row["source_url_identity_status"], "MATCH")
        self.assertEqual(row["manual_review_status"], "PENDING")
        self.assertEqual(row["observed_value"], "")

    def test_value_mismatch_is_flagged(self):
        self.row["value"] = "123457"
        with self.sample_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.row, lineterminator="\n")
            writer.writeheader()
            writer.writerow(self.row)
        validate_financial_fact_sample(self.sample_path, self.output_path)
        self.assertEqual(self.read_output()["automated_validation_status"], "REVIEW_MISMATCH_OR_MISSING_FACT")

    def test_tampered_snapshot_hash_is_flagged(self):
        self.raw_path.write_text(self.raw_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        validate_financial_fact_sample(self.sample_path, self.output_path)
        row = self.read_output()
        self.assertEqual(row["automated_validation_status"], "REVIEW_ARTIFACT_PROBLEM")
        self.assertIn("Hash mismatch", row["automated_validation_details"])

    def test_bad_url_accession_is_flagged_without_upgrading_manual_status(self):
        self.row["source_url"] = "https://www.sec.gov/Archives/edgar/data/1/000000000124999999/report.htm"
        with self.sample_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.row, lineterminator="\n")
            writer.writeheader()
            writer.writerow(self.row)
        validate_financial_fact_sample(self.sample_path, self.output_path)
        row = self.read_output()
        self.assertEqual(row["automated_validation_status"], "REVIEW_SOURCE_URL_IDENTITY")
        self.assertEqual(row["manual_review_status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
