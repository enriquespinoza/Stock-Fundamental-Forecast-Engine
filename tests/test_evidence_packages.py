import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from stock_analyzer.evidence_packages import build_evidence_packages


class EvidencePackageTests(unittest.TestCase):
    def test_builds_fact_derived_and_signal_evidence_with_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            briefs = root / "issuer_fundamental_briefs.jsonl"
            source_fact = {
                "metric": "revenue",
                "status": "AVAILABLE", "value": "80", "unit": "USD",
                "period_start": "2024-01-01", "period_end": "2024-12-31",
                "form": "10-K", "tag": "Revenue", "accession_number": "0000000001-25-000001",
                "available_after_utc": "2025-02-01T00:01:00Z",
                "source_url": "https://www.sec.gov/Archives/example.htm",
            }
            signal_fact = {key: value for key, value in source_fact.items()
                           if key not in ("status", "available_after_utc")}
            signal_fact["available_after_utc"] = source_fact["available_after_utc"]
            signal_fact["value"] = "100"
            signal_fact["period_start"] = "2023-01-01"
            signal_fact["period_end"] = "2023-12-31"
            signal_fact["accession_number"] = "0000000001-24-000001"
            signal = {
                "code": "REVENUE_DECLINED_TWICE", "status": "FLAG_FOR_REVIEW",
                "description": "Revenue declined across annual comparisons.",
                "periods": ["2023-12-31", "2024-12-31"],
                "accessions": [signal_fact["accession_number"], source_fact["accession_number"]],
                "evidence": [signal_fact, {**source_fact, "value": "80"}],
                "year_over_year_changes": [{"change_percent": -20.0}],
            }
            metric = {"latest": source_fact, "prior": {"status": "INSUFFICIENT_DATA"},
                      "change_percent": None}
            brief = {
                "schema_version": "issuer-fundamental-brief-v1", "issuer_symbol": "AAA",
                "issuer_cik": "0000000001", "as_of_utc": "2025-03-01T00:00:00Z",
                "annual_10k": {"metrics": {"revenue": metric},
                               "period_aligned_ratios": {"operating_margin": {
                                   "value": 0.2, "period_end": "2024-12-31", "period_start": "2024-01-01",
                                   "numerator_accession": "0000000001-25-000001",
                                   "denominator_accession": "0000000001-25-000001"}}},
                "quarterly_10q": {"metrics": {}, "period_aligned_ratios": {}},
                "review_signals": [signal], "data_quality": {"annual_metrics_available": 1},
            }
            briefs.write_text(json.dumps(brief) + "\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")

            result = build_evidence_packages(briefs, root / "out", brief_manifest_path=manifest,
                                             built_at_utc="2025-03-01T00:00:00Z")
            package = json.loads((result["output_dir"] / "issuer_evidence_packages.jsonl")
                                 .read_text(encoding="utf-8").splitlines()[0])
            items = package["evidence_items"]
            reported = [item for item in items if item["evidence_type"] == "reported_fact"]
            derived = [item for item in items if item["evidence_type"] == "derived_metric"]
            prompt = next(item for item in items if item["evidence_type"] == "rule_generated_review_prompt")
            self.assertEqual(len(reported), 2)
            self.assertEqual({item["source"]["url"] for item in reported},
                             {"https://www.sec.gov/Archives/example.htm"})
            self.assertEqual(len(derived), 1)
            self.assertEqual(set(prompt["evidence_refs"]), {item["evidence_id"] for item in reported})
            self.assertTrue(all(item["availability"]["point_in_time_eligible"] for item in items))
            self.assertEqual(result["summary"]["package_count"], 1)
            manifest_data = json.loads((result["output_dir"] / "manifest.json").read_text())
            self.assertEqual(manifest_data["source_briefs"]["sha256"],
                             hashlib.sha256(briefs.read_bytes()).hexdigest())

    def test_rejects_future_fact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            briefs = root / "briefs.jsonl"
            brief = {"issuer_symbol": "AAA", "issuer_cik": "0000000001",
                     "as_of_utc": "2025-03-01T00:00:00Z",
                     "annual_10k": {"metrics": {"revenue": {"latest": {
                         "status": "AVAILABLE", "value": "1", "unit": "USD",
                         "period_end": "2024-12-31", "form": "10-K", "tag": "Revenue",
                         "accession_number": "0000000001-25-000001",
                         "available_after_utc": "2025-04-01T00:00:00Z"}}},
                         "period_aligned_ratios": {}},
                     "quarterly_10q": {"metrics": {}, "period_aligned_ratios": {}},
                     "review_signals": []}
            briefs.write_text(json.dumps(brief) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Future fact"):
                build_evidence_packages(briefs, root / "out", built_at_utc="2025-03-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
