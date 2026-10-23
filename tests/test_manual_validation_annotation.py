import csv
import tempfile
import unittest
from pathlib import Path

from stock_analyzer.manual_validation_annotation import annotate_financial_fact


class ManualValidationAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sample = self.root / "financial_fact_review_sample.csv"
        self.output = self.root / "financial_fact_review_sample_reviewed.csv"
        self.row = {
            "issuer_cik": "0000200406",
            "accession_number": "0000200406-17-000006",
            "form": "10-K",
            "filing_date": "2017-02-27",
            "metric": "assets",
            "tag": "Assets",
            "value": "141208000000",
            "unit": "USD",
            "period_kind": "instant",
            "period_start": "",
            "period_end": "2017-01-01",
            "source_url": "https://www.sec.gov/Archives/edgar/data/200406/000020040617000006/form10-k20170101.htm",
            "manual_review_status": "PENDING",
            "observed_tag": "",
            "observed_value": "",
            "observed_unit": "",
            "observed_period_start": "",
            "observed_period_end": "",
            "review_notes": "",
        }
        with self.sample.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.row, lineterminator="\n")
            writer.writeheader()
            writer.writerow(self.row)

    def tearDown(self):
        self.temp.cleanup()

    def annotate(self, **overrides):
        values = {
            "accession_number": self.row["accession_number"],
            "metric": "assets",
            "tag": "Assets",
            "period_start": "",
            "period_end": "2017-01-01",
            "observed_tag": "Assets",
            "observed_value": "141208000000",
            "observed_unit": "USD",
            "observed_period_start": "",
            "observed_period_end": "2017-01-01",
            "review_notes": "Matched consolidated balance sheet; reported in millions.",
            "source_checked": True,
        }
        values.update(overrides)
        return annotate_financial_fact(self.sample, self.output, **values)

    def rows(self, path):
        with path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_creates_reviewed_copy_without_modifying_sample(self):
        original = self.sample.read_bytes()
        result = self.annotate()
        reviewed = self.rows(self.output)[0]
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(reviewed["manual_review_status"], "CONFIRMED")
        self.assertEqual(reviewed["observed_value"], "141208000000")
        self.assertEqual(self.sample.read_bytes(), original)

    def test_requires_source_check(self):
        with self.assertRaisesRegex(ValueError, "SEC source has been checked"):
            self.annotate(source_checked=False)
        self.assertFalse(self.output.exists())

    def test_confirmed_row_rejects_value_mismatch(self):
        with self.assertRaisesRegex(ValueError, "observed_value differs"):
            self.annotate(observed_value="141207000000")

    def test_reviewed_copy_can_be_updated_for_another_annotation(self):
        self.annotate()
        self.annotate(review_notes="Confirmed after checking source statement.")
        self.assertEqual(self.rows(self.output)[0]["review_notes"], "Confirmed after checking source statement.")

    def test_refuses_to_overwrite_source_sample(self):
        with self.assertRaisesRegex(ValueError, "separate reviewed copy"):
            annotate_financial_fact(self.sample, self.sample, **{
                "accession_number": self.row["accession_number"], "metric": "assets",
                "tag": "Assets", "period_start": "", "period_end": "2017-01-01",
                "observed_tag": "Assets", "observed_value": "141208000000",
                "observed_unit": "USD", "observed_period_start": "",
                "observed_period_end": "2017-01-01", "review_notes": "checked",
                "source_checked": True,
            })


if __name__ == "__main__":
    unittest.main()
