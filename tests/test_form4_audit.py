import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.form4_audit import audit_form4_history


class Form4AuditTests(unittest.TestCase):
    def test_audit_checks_hashes_summarizes_transactions_and_classifies_legacy_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "filing_ledger.jsonl"
            supported = {"accession_number": "0000000001-26-000001", "form": "4",
                         "filing_date": "2026-10-01", "primary_document": "ownership.xml"}
            legacy = {"accession_number": "0000000001-03-000002", "form": "4",
                      "filing_date": "2003-03-21", "primary_document": "j8739_4.htm"}
            ledger_raw = (json.dumps(supported) + "\n" + json.dumps(legacy) + "\n").encode()
            ledger_path.write_bytes(ledger_raw)

            raw_path = root / "ownership.xml"
            raw = b"<ownershipDocument/>"
            raw_path.write_bytes(raw)
            normalized_root = root / "normalized"
            output = normalized_root / "form4" / "2026-10-01T00-00-00Z-1"
            output.mkdir(parents=True)
            normalized = {"schema_version": "form4-document-v1",
                          "filing": {"accession_number": supported["accession_number"],
                                     "raw_sha256": hashlib.sha256(raw).hexdigest(),
                                     "aff10b5one": True, "is_amendment": False,
                                     "reporting_owners": [{"is_officer": True, "officer_title": "CFO"}]},
                          "transactions": [{"transaction_code": "S", "transaction_class": "sale",
                                            "is_purchase_sale_candidate": True, "quality_flags": []}],
                          "holdings": []}
            normalized_raw = (json.dumps(normalized, sort_keys=True, indent=2) + "\n").encode()
            (output / "normalized.json").write_bytes(normalized_raw)
            summary = {"selected_count": 1, "results": [{"accession_number": legacy["accession_number"],
                       "return_code": 1}]}
            summary_path = root / "batch.json"
            summary_path.write_text(json.dumps(summary))
            manifest = {"command": "form4", "ledger_sha256": hashlib.sha256(ledger_raw).hexdigest(),
                        "normalized_sha256": hashlib.sha256(normalized_raw).hexdigest(),
                        "source": {"path": str(raw_path), "sha256": hashlib.sha256(raw).hexdigest()}}
            (output / "manifest.json").write_text(json.dumps(manifest))

            report = audit_form4_history(ledger_path, normalized_root,
                                         batch_summary_path=summary_path, base_dir=root)

            self.assertEqual(report["ledger"]["form4_count"], 2)
            self.assertEqual(report["artifacts"]["unique_normalized_accessions"], 1)
            self.assertEqual(report["artifacts"]["missing_accessions"], [legacy["accession_number"]])
            self.assertEqual(report["artifacts"]["missing_supported_accessions"], [])
            self.assertEqual(report["artifacts"]["integrity_issues"], [])
            self.assertEqual(report["batch"]["unsupported_source_format"], [{
                "accession_number": legacy["accession_number"], "filing_date": "2003-03-21",
                "primary_document": "j8739_4.htm", "status": "UNSUPPORTED_SOURCE_FORMAT",
                "batch_failed": True}])
            self.assertEqual(report["filings"]["owner_role_counts"], {"officer": 1})
            self.assertEqual(report["filings"]["candidate_counts_by_plan"], {"sale|10b5-1=True": 1})

    def test_detects_modified_normalized_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.jsonl"
            row = {"accession_number": "0000000001-26-000001", "form": "4", "primary_document": "a.xml"}
            ledger.write_text(json.dumps(row) + "\n")
            output = root / "normalized" / "form4" / "run"
            output.mkdir(parents=True)
            normalized = {"filing": {"accession_number": row["accession_number"], "raw_sha256": "0" * 64},
                          "transactions": []}
            normalized_path = output / "normalized.json"
            normalized_path.write_text(json.dumps(normalized))
            raw_path = root / "present-but-different.xml"
            raw_path.write_bytes(b"not the declared raw content")
            manifest = {"command": "form4", "normalized_sha256": "f" * 64,
                        "ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
                        "source": {"path": str(raw_path), "sha256": "0" * 64}}
            (output / "manifest.json").write_text(json.dumps(manifest))

            report = audit_form4_history(ledger, root / "normalized", base_dir=root)

            reasons = {issue["reason"] for issue in report["artifacts"]["integrity_issues"]}
            self.assertIn("NORMALIZED_HASH_VALID", reasons)
            self.assertIn("SOURCE_FILE_VERIFIED", reasons)


if __name__ == "__main__":
    unittest.main()
