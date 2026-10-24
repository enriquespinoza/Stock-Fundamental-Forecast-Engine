import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.point_in_time import join_form4_events_to_financials


def fact(metric, value, start, end, kind, accession, tag, available, *, tag_preference=0, form="10-Q"):
    return {"schema_version": "financial-fact-v1", "issuer_cik": "0000000001", "metric": metric,
            "unit": "USD", "value": str(value), "period_start": start, "period_end": end,
            "period_kind": kind, "accession_number": accession, "form": form, "tag": tag,
            "tag_preference": tag_preference, "accepted_at_utc": available,
            "historical_available_after_utc": available, "live_available_after_utc": available}


class PointInTimeJoinTests(unittest.TestCase):
    def test_selects_only_known_facts_and_prefers_quarter_over_ytd_tie(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_dir = root / "event-run"
            event_dir.mkdir()
            events_path = event_dir / "candidate_transactions.csv"
            with events_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("event_id", "issuer_cik", "historical_available_after_utc"))
                writer.writeheader()
                writer.writerow({"event_id": "event-1", "issuer_cik": "0000000001",
                                 "historical_available_after_utc": "2026-09-10T12:00:00Z"})
            event_raw = events_path.read_bytes()
            (event_dir / "manifest.json").write_text(json.dumps({"output": {
                "sha256": hashlib.sha256(event_raw).hexdigest(), "row_count": 1}}))

            financial_dir = root / "financial-run"
            financial_dir.mkdir()
            financial_path = financial_dir / "normalized.json"
            facts = [
                fact("revenue", "900", "2025-07-01", "2026-06-30", "annual", "accn-10k",
                     "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-08-01T12:00:00Z",
                     form="10-K"),
                fact("revenue", "1100", "2025-07-01", "2026-06-30", "annual", "accn-10q-comparative",
                     "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-09-01T12:00:00Z",
                     form="10-Q"),
                fact("revenue", "320", "2026-04-01", "2026-06-30", "quarter", "accn-q3-quarter",
                     "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-07-21T12:00:00Z"),
                fact("revenue", "100", "2026-01-01", "2026-03-31", "quarter", "accn-q2",
                     "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-07-21T12:00:00Z"),
                fact("revenue", "250", "2026-01-01", "2026-06-30", "ytd_half_year", "accn-q2",
                     "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-07-21T12:00:00Z"),
                fact("revenue", "120", "2026-04-01", "2026-06-30", "quarter", "accn-amend",
                     "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-08-01T12:00:00Z"),
                fact("operating_cash_flow", "50", "2026-04-01", "2026-06-30", "quarter", "accn-q2",
                     "NetCashProvidedByUsedInOperatingActivities", "2026-07-21T12:00:00Z"),
                fact("capital_expenditures", "10", "2026-04-01", "2026-06-30", "quarter", "accn-q2",
                     "PaymentsToAcquirePropertyPlantAndEquipment", "2026-07-21T12:00:00Z"),
            ]
            financial_raw = (json.dumps({"schema_version": "financial-normalization-v1",
                                         "issuer_cik": "0000000001", "facts": facts,
                                         "issues": [{"reason": "UNSUPPORTED_FORM"}]},
                                        sort_keys=True, indent=2) + "\n").encode()
            financial_path.write_bytes(financial_raw)
            (financial_dir / "manifest.json").write_text(json.dumps({
                "normalized_sha256": hashlib.sha256(financial_raw).hexdigest()}))

            result = join_form4_events_to_financials(events_path, financial_path, root / "joins",
                                                     built_at_utc="2026-10-20T00:00:00Z")
            with result["csv_path"].open(encoding="utf-8", newline="") as stream:
                rows = {row["metric"]: row for row in csv.DictReader(stream)}

            self.assertEqual(rows["revenue"]["value"], "120")
            self.assertEqual(rows["revenue"]["period_kind"], "quarter")
            self.assertEqual(rows["revenue"]["fact_accession_number"], "accn-amend")
            self.assertEqual(rows["revenue_10k"]["value"], "900")
            self.assertEqual(rows["revenue_10k"]["fact_accession_number"], "accn-10k")
            self.assertEqual(rows["revenue_10k"]["fact_form"], "10-K")
            self.assertEqual(rows["revenue_10q"]["value"], "120")
            self.assertEqual(rows["revenue_10q"]["period_kind"], "quarter")
            self.assertEqual(rows["free_cash_flow"]["value"], "40")
            self.assertEqual(rows["free_cash_flow"]["fact_accession_number"], "accn-q2")
            self.assertEqual(rows["free_cash_flow_10q"]["value"], "40")
            self.assertEqual(rows["free_cash_flow_10k"]["selection_status"], "NO_ELIGIBLE_FACT_AS_OF_EVENT")
            self.assertEqual(rows["assets"]["selection_status"], "NO_ELIGIBLE_FACT_AS_OF_EVENT")
            self.assertEqual(result["summary"]["event_count"], 1)
            self.assertEqual(result["summary"]["event_metric_rows"], 33)
            self.assertEqual(result["summary"]["selected_fact_rows"], 9)
            self.assertEqual(result["summary"]["selected_base_fact_rows"], 4)
            self.assertEqual(result["summary"]["selected_10k_fact_rows"], 1)
            self.assertEqual(result["summary"]["selected_10q_fact_rows"], 4)
            self.assertEqual(result["summary"]["financials_with_issues"], 1)

    def test_rejects_event_and_financial_issuer_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_dir = root / "event-run"
            event_dir.mkdir()
            events_path = event_dir / "candidate_transactions.csv"
            with events_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("event_id", "issuer_cik", "historical_available_after_utc"))
                writer.writeheader()
                writer.writerow({"event_id": "event-1", "issuer_cik": "0000000002",
                                 "historical_available_after_utc": "2026-07-22T12:00:00Z"})
            raw = events_path.read_bytes()
            (event_dir / "manifest.json").write_text(json.dumps({"output": {
                "sha256": hashlib.sha256(raw).hexdigest(), "row_count": 1}}))
            financial_dir = root / "financial-run"
            financial_dir.mkdir()
            financial_path = financial_dir / "normalized.json"
            financial_raw = json.dumps({"schema_version": "financial-normalization-v1",
                                        "issuer_cik": "0000000001", "facts": []}).encode()
            financial_path.write_bytes(financial_raw)
            (financial_dir / "manifest.json").write_text(json.dumps({
                "normalized_sha256": hashlib.sha256(financial_raw).hexdigest()}))

            with self.assertRaisesRegex(ValueError, "issuer does not match"):
                join_form4_events_to_financials(events_path, financial_path, root / "joins")


if __name__ == "__main__":
    unittest.main()
