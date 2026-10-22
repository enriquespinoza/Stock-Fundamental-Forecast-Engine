import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.form4_events import build_form4_event_dataset


def write_run(root, run_name, accession, form, transactions):
    run = root / "form4" / run_name
    run.mkdir(parents=True)
    filing = {"accession_number": accession, "issuer_cik": "0000000001", "issuer_symbol": "FIX",
              "form": form, "filing_date": "2026-10-09", "accepted_at_utc": "2026-10-09T20:30:00Z",
              "historical_available_after_utc": "2026-10-09T20:31:00Z",
              "observed_at_utc": "2026-10-10T00:00:00Z", "raw_sha256": "a" * 64,
              "source_url": "https://www.sec.gov/example.xml", "is_amendment": form == "4/A",
              "aff10b5one": None, "reporting_owners": [{"cik": "0000000002", "name": "Owner",
              "is_officer": True, "is_director": False, "is_ten_percent_owner": False,
              "is_other": False, "officer_title": "CFO"}]}
    normalized = {"schema_version": "form4-document-v1", "filing": filing,
                  "transactions": transactions, "holdings": []}
    raw = (json.dumps(normalized, indent=2, sort_keys=True) + "\n").encode()
    (run / "normalized.json").write_bytes(raw)
    manifest = {"command": "form4", "normalized_sha256": hashlib.sha256(raw).hexdigest(),
                "source": {"sha256": "a" * 64}}
    (run / "manifest.json").write_text(json.dumps(manifest))


def transaction(record_id, code, candidate, *, flags=None):
    return {"record_id": record_id, "transaction_code": code,
            "transaction_class": "sale" if code == "S" else "purchase" if code == "P" else "award_or_grant",
            "is_purchase_sale_candidate": candidate, "is_derivative": False,
            "quality_flags": flags or [], "shares": "10", "price_per_share": "12.50",
            "reported_gross_value": "125.0", "transaction_date": "2026-10-07",
            "historical_available_after_utc": None, "acquired_disposed": "D" if code == "S" else "A",
            "security_title": "Common Stock", "shares_owned_after": "90", "ownership_type": "D"}


class Form4EventTests(unittest.TestCase):
    def test_exports_candidate_rows_with_availability_context_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normalized_root = root / "normalized"
            accession = "0000000001-26-000001"
            write_run(normalized_root, "2026-10-01-run1", accession, "4", [
                transaction(f"{accession}:sale", "S", True),
                transaction(f"{accession}:buy", "P", True),
                transaction(f"{accession}:award", "A", False),
            ])
            # A later run for the same accession must replace, not duplicate, rows.
            write_run(normalized_root, "2026-10-02-run2", accession, "4", [
                transaction(f"{accession}:sale", "S", True),
                transaction(f"{accession}:amended-sale", "S", False,
                            flags=["AMENDMENT_REQUIRES_RECONCILIATION"]),
            ])

            result = build_form4_event_dataset(normalized_root, root / "events",
                                               built_at_utc="2026-10-20T00:00:00Z")

            self.assertEqual(result["summary"]["normalized_artifact_count"], 2)
            self.assertEqual(result["summary"]["unique_normalized_accessions"], 1)
            self.assertEqual(result["summary"]["duplicate_artifact_count"], 1)
            self.assertEqual(result["summary"]["candidate_event_count"], 1)
            self.assertEqual(result["summary"]["candidate_sale_count"], 1)
            self.assertEqual(result["summary"]["excluded_transaction_counts"], {
                "AMENDMENT_REQUIRES_RECONCILIATION": 1})
            with result["csv_path"].open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["historical_available_after_utc"], "2026-10-09T20:31:00Z")
            self.assertEqual(rows[0]["transaction_date"], "2026-10-07")
            self.assertEqual(rows[0]["aff10b5one"], "")
            self.assertEqual(result["summary"]["return_labels"], "NOT_CREATED")

    def test_accepts_full_submission_hash_with_extracted_xml_document_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normalized_root = root / "normalized"
            accession = "0000000001-26-000003"
            write_run(normalized_root, "run", accession, "4", [
                transaction(f"{accession}:buy", "P", True),
            ])
            manifest_path = normalized_root / "form4" / "run" / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["source"] = {
                "sha256": "b" * 64,
                "document_sha256": "a" * 64,
            }
            manifest_path.write_text(json.dumps(manifest))

            result = build_form4_event_dataset(normalized_root, root / "events",
                                               built_at_utc="2026-10-20T00:00:00Z")
            self.assertEqual(result["summary"]["candidate_event_count"], 1)

    def test_rejects_candidate_amendments_and_bad_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normalized_root = root / "normalized"
            accession = "0000000001-26-000002"
            write_run(normalized_root, "run", accession, "4/A", [transaction("amend-sale", "S", True)])
            with self.assertRaisesRegex(ValueError, "Amendment transactions"):
                build_form4_event_dataset(normalized_root, root / "events")

            manifest = normalized_root / "form4" / "run" / "manifest.json"
            manifest_data = json.loads(manifest.read_text())
            manifest_data["normalized_sha256"] = "f" * 64
            manifest.write_text(json.dumps(manifest_data))
            with self.assertRaisesRegex(ValueError, "Normalized output hash mismatch"):
                build_form4_event_dataset(normalized_root, root / "events")


if __name__ == "__main__":
    unittest.main()
