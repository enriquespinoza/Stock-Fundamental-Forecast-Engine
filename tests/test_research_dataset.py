import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.research_dataset import build_event_research_dataset


class EventResearchDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.event_dir = self.root / "events"
        self.financial_dir = self.root / "financials"
        self.label_dir = self.root / "labels"
        for path in (self.event_dir, self.financial_dir, self.label_dir):
            path.mkdir()
        self.event_path = self.event_dir / "candidate_transactions.csv"
        self.financial_path = self.financial_dir / "event_financial_features.csv"
        self.labels_path = self.label_dir / "event_return_labels.csv"
        self.events = [
            {"event_id": "e1", "issuer_cik": "0000000001", "issuer_symbol": "TEST",
             "historical_available_after_utc": "2026-01-02T12:00:00Z", "transaction_code": "P"},
            {"event_id": "e2", "issuer_cik": "0000000001", "issuer_symbol": "TEST",
             "historical_available_after_utc": "2026-01-03T12:00:00Z", "transaction_code": "S"},
        ]
        self.metrics = ["assets", "revenue"]
        self.financial_rows = []
        for event in self.events:
            for metric in self.metrics:
                selected = metric == "assets"
                self.financial_rows.append({
                    "event_id": event["event_id"], "issuer_cik": event["issuer_cik"],
                    "event_available_at_utc": event["historical_available_after_utc"],
                    "metric": metric, "value": "1000" if selected else "",
                    "unit": "USD" if selected else "", "period_kind": "instant" if selected else "",
                    "period_start": "", "period_end": "2025-12-31" if selected else "",
                    "fact_accession_number": "0000000001-25-000001" if selected else "",
                    "fact_form": "10-K" if selected else "", "tag": "Assets" if selected else "",
                    "tag_preference": "preferred" if selected else "",
                    "fact_available_after_utc": "2026-01-01T12:00:00Z" if selected else "",
                    "fact_age_days": "1.000000" if selected else "",
                    "selection_status": "SELECTED_AS_OF_EVENT" if selected else "NO_ELIGIBLE_FACT_AS_OF_EVENT",
                })
        self.label_rows = [
            {"event_id": event["event_id"],
             "event_available_at_utc": event["historical_available_after_utc"],
             "primary_sample_eligible": "true", "label_status": "LABELED",
             "forward_return_1d": "0.01", "label_status_1d": "OK",
             "label_status_5d": "OK", "label_status_20d": "OK"}
            for event in self.events
        ]
        self._write_all()

    @staticmethod
    def _write_csv(path, rows):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        raw = path.read_bytes()
        return raw, hashlib.sha256(raw).hexdigest()

    def _write_all(self, *, label_event_hash=None):
        event_raw, event_hash = self._write_csv(self.event_path, self.events)
        financial_raw, financial_hash = self._write_csv(self.financial_path, self.financial_rows)
        labels_raw, labels_hash = self._write_csv(self.labels_path, self.label_rows)
        (self.event_dir / "manifest.json").write_text(json.dumps({"output": {
            "sha256": event_hash, "row_count": len(self.events)}}))
        (self.financial_dir / "manifest.json").write_text(json.dumps({
            "event_input": {"sha256": event_hash},
            "output": {"sha256": financial_hash, "row_count": len(self.financial_rows)}}))
        (self.label_dir / "manifest.json").write_text(json.dumps({
            "event_input": {"sha256": label_event_hash or event_hash},
            "output": {"sha256": labels_hash, "row_count": len(self.label_rows)}}))
        return event_hash

    def test_widens_financial_metrics_and_joins_labels_by_event_id(self):
        result = build_event_research_dataset(
            self.event_path, self.financial_path, self.labels_path, self.root / "output",
            built_at_utc="2026-02-01T00:00:00Z")
        with result["csv_path"].open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event_id"], "e1")
        self.assertEqual(rows[0]["financial_assets_value"], "1000")
        self.assertEqual(rows[0]["financial_revenue_selection_status"], "NO_ELIGIBLE_FACT_AS_OF_EVENT")
        self.assertEqual(rows[1]["label_forward_return_1d"], "0.01")
        self.assertEqual(result["summary"]["financial_metric_count"], 2)
        self.assertEqual(result["summary"]["missing_financial_fact_rows"], 2)

    def test_rejects_future_financial_fact(self):
        selected = self.financial_rows[0]
        selected["fact_available_after_utc"] = "2026-01-04T12:00:00Z"
        self._write_all()
        with self.assertRaisesRegex(ValueError, "Future or missing financial availability"):
            build_event_research_dataset(
                self.event_path, self.financial_path, self.labels_path, self.root / "output")

    def test_rejects_labels_built_from_different_event_snapshot(self):
        event_hash = self._write_all(label_event_hash="0" * 64)
        self.assertNotEqual(event_hash, "0" * 64)
        with self.assertRaisesRegex(ValueError, "different event CSV"):
            build_event_research_dataset(
                self.event_path, self.financial_path, self.labels_path, self.root / "output")


if __name__ == "__main__":
    unittest.main()
