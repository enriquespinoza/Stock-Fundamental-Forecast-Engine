import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.manual_validation_sample import build_manual_validation_sample


class ManualValidationSampleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.eight_k_root = self.root / "normalized" / "8k"
        self.financials_root = self.root / "normalized" / "financials"
        self.eight_k_root.mkdir(parents=True)
        self.financials_root.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _add_8k(self, accession, cik, year, form, items):
        run = self.eight_k_root / f"run-{accession}"
        run.mkdir()
        source = run / "source.htm"
        source.write_text("FORM 8-K. Item 2.02 Results of Operations and Financial Condition.", encoding="utf-8")
        source_bytes = source.read_bytes()
        digest = hashlib.sha256(source_bytes).hexdigest()
        normalized = {
            "schema_version": "8k-item-index-v1", "issuer_cik": cik,
            "accession_number": accession, "form": form, "filing_date": f"{year}-05-01",
            "historical_available_after_utc": f"{year}-05-02T00:00:00Z",
            "historical_research_eligible": True, "source_sha256": digest,
            "items": items,
        }
        raw = json.dumps(normalized).encode()
        path = run / "normalized.json"
        path.write_bytes(raw)
        (run / "manifest.json").write_text(json.dumps({
            "output": {"sha256": hashlib.sha256(raw).hexdigest()},
            "source": {"path": str(source), "sha256": digest},
        }), encoding="utf-8")

    def _add_financial_run(self):
        for cik, accession, form, filed, metric, period_kind in (
            ("0000000001", "0000000001-20-000001", "10-Q", "2020-04-30", "revenue", "quarter"),
            ("0000000002", "0000000002-20-000002", "10-K", "2020-02-01", "assets", "instant"),
        ):
            run = self.financials_root / f"{cik}-2026-10-22T00-00-00Z-1"
            run.mkdir()
            fact = {
                "issuer_cik": cik, "accession_number": accession, "form": form,
                "filed_date": filed, "historical_available_after_utc": filed + "T00:01:00Z",
                "metric": metric, "tag": metric, "tag_preference": 0, "value": "123",
                "unit": "USD", "period_kind": period_kind, "period_start": "2020-01-01" if form == "10-Q" else None,
                "period_end": filed, "fiscal_year": 2020, "fiscal_period": "Q1" if form == "10-Q" else "FY",
                "source_url": "https://www.sec.gov/Archives/example.htm",
            }
            normalized = {"schema_version": "financial-normalization-v1", "issuer_cik": cik, "facts": [fact]}
            raw = json.dumps(normalized).encode()
            path = run / "normalized.json"
            path.write_bytes(raw)
            (run / "manifest.json").write_text(json.dumps({"normalized_sha256": hashlib.sha256(raw).hexdigest(), "source": {}}), encoding="utf-8")

    def test_seeded_sample_covers_target_and_negative_8k_and_financial_strata(self):
        self._add_8k("0000000001-25-000001", "0000000001", 2025, "8-K", [
            {"item": "2.02", "family": "earnings_or_financial_update", "mention_count": 1},
            {"item": "9.01", "family": "exhibits", "mention_count": 1},
        ])
        self._add_8k("0000000002-25-000002", "0000000002", 2025, "8-K/A", [
            {"item": "5.02", "family": "director_or_officer_change", "mention_count": 1},
        ])
        self._add_8k("0000000003-25-000003", "0000000003", 2025, "8-K", [
            {"item": "9.01", "family": "exhibits", "mention_count": 1},
        ])
        self._add_financial_run()
        out = self.root / "sample"
        result = build_manual_validation_sample(self.eight_k_root, self.financials_root, out, seed=7)
        with (out / "8k_filing_review_sample.csv").open(encoding="utf-8", newline="") as stream:
            filings = list(csv.DictReader(stream))
        with (out / "financial_fact_review_sample.csv").open(encoding="utf-8", newline="") as stream:
            facts = list(csv.DictReader(stream))
        self.assertEqual({r["sample_family"] for r in filings}, {
            "earnings_or_financial_update", "director_or_officer_change", "NO_TARGET_ITEM",
        })
        self.assertTrue(all(r["manual_review_status"] == "PENDING" for r in filings + facts))
        self.assertEqual({r["form"] for r in facts}, {"10-Q", "10-K"})
        self.assertEqual(result["sample_counts"], {"8k_filing_units": 3, "financial_fact_rows": 2})
        self.assertEqual(result["seed"], 7)
        self.assertTrue((out / "manifest.json").is_file())

    def test_same_inputs_and_seed_select_identical_units(self):
        self._add_8k("0000000001-25-000001", "0000000001", 2025, "8-K", [
            {"item": "2.02", "family": "earnings_or_financial_update", "mention_count": 1},
        ])
        self._add_financial_run()
        one, two = self.root / "one", self.root / "two"
        build_manual_validation_sample(self.eight_k_root, self.financials_root, one, seed=3)
        build_manual_validation_sample(self.eight_k_root, self.financials_root, two, seed=3)
        self.assertEqual((one / "8k_filing_review_sample.csv").read_bytes(), (two / "8k_filing_review_sample.csv").read_bytes())
        self.assertEqual((one / "financial_fact_review_sample.csv").read_bytes(), (two / "financial_fact_review_sample.csv").read_bytes())


if __name__ == "__main__":
    unittest.main()
