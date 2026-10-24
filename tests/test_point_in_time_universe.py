import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.point_in_time_universe import join_form4_events_to_financial_universe


def write_financial_run(root: Path, run_name: str, cik: str, value: str) -> Path:
    folder = root / run_name
    folder.mkdir(parents=True)
    fact = {
        "schema_version": "financial-fact-v1", "issuer_cik": cik, "metric": "assets",
        "unit": "USD", "value": value, "period_start": None, "period_end": "2026-06-30",
        "period_kind": "instant", "accession_number": f"acc-{cik}", "form": "10-Q",
        "tag": "Assets", "tag_preference": 0, "accepted_at_utc": "2026-07-20T00:00:00Z",
        "historical_available_after_utc": "2026-07-21T00:00:00Z",
        "live_available_after_utc": "2026-07-21T00:00:00Z",
    }
    raw = (json.dumps({"schema_version": "financial-normalization-v1", "issuer_cik": cik,
                       "facts": [fact], "issues": []}, sort_keys=True, indent=2) + "\n").encode()
    path = folder / "normalized.json"
    path.write_bytes(raw)
    (folder / "manifest.json").write_text(json.dumps({"command": "financials",
        "normalized_sha256": hashlib.sha256(raw).hexdigest()}))
    return path


class PointInTimeUniverseTests(unittest.TestCase):
    def test_joins_only_matching_issuer_and_preserves_point_in_time_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events_dir = root / "events"
            events_dir.mkdir()
            events_path = events_dir / "candidate_transactions.csv"
            with events_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("event_id", "issuer_cik", "historical_available_after_utc"))
                writer.writeheader()
                writer.writerow({"event_id": "event-a", "issuer_cik": "0000000001",
                                 "historical_available_after_utc": "2026-07-22T00:00:00Z"})
                writer.writerow({"event_id": "event-b", "issuer_cik": "0000000002",
                                 "historical_available_after_utc": "2026-07-20T00:00:00Z"})
            event_raw = events_path.read_bytes()
            (events_dir / "manifest.json").write_text(json.dumps({"output": {
                "sha256": hashlib.sha256(event_raw).hexdigest(), "row_count": 2}}))
            financial_root = root / "financials"
            write_financial_run(financial_root, "2026-10-01T00-00-00Z-1", "0000000001", "100")
            write_financial_run(financial_root, "2026-10-01T00-00-00Z-2", "0000000002", "200")

            result = join_form4_events_to_financial_universe(events_path, financial_root, root / "joins",
                                                             built_at_utc="2026-10-02T00:00:00Z")
            with result["csv_path"].open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            by_event_metric = {(row["event_id"], row["metric"]): row for row in rows}
            self.assertEqual(by_event_metric[("event-a", "assets")]["value"], "100")
            self.assertEqual(by_event_metric[("event-a", "assets_10q")]["value"], "100")
            self.assertEqual(by_event_metric[("event-a", "assets_10k")]["selection_status"],
                             "NO_ELIGIBLE_FACT_AS_OF_EVENT")
            self.assertEqual(by_event_metric[("event-b", "assets")]["value"], "")
            self.assertEqual(by_event_metric[("event-b", "assets")]["selection_status"], "NO_ELIGIBLE_FACT_AS_OF_EVENT")
            self.assertEqual(len(rows), 66)
            self.assertEqual(result["summary"]["metric_count"], 33)
            self.assertEqual(result["summary"]["selected_base_fact_rows"], 1)
            self.assertEqual(result["summary"]["selected_10k_fact_rows"], 0)
            self.assertEqual(result["summary"]["selected_10q_fact_rows"], 1)
            self.assertEqual(result["summary"]["event_counts_by_issuer_cik"], {"0000000001": 1, "0000000002": 1})
            self.assertEqual(len(result["manifest"]["financials_inputs"]), 2)

    def test_missing_issuer_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events_dir = root / "events"
            events_dir.mkdir()
            events_path = events_dir / "candidate_transactions.csv"
            with events_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("event_id", "issuer_cik", "historical_available_after_utc"))
                writer.writeheader()
                writer.writerow({"event_id": "event-a", "issuer_cik": "0000000002",
                                 "historical_available_after_utc": "2026-07-22T00:00:00Z"})
            event_raw = events_path.read_bytes()
            (events_dir / "manifest.json").write_text(json.dumps({"output": {
                "sha256": hashlib.sha256(event_raw).hexdigest(), "row_count": 1}}))
            write_financial_run(root / "financials", "2026-10-01T00-00-00Z-1", "0000000001", "100")
            with self.assertRaisesRegex(ValueError, "Missing normalized financial histories"):
                join_form4_events_to_financial_universe(events_path, root / "financials", root / "joins")


if __name__ == "__main__":
    unittest.main()
