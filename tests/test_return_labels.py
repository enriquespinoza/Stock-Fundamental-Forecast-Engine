import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.return_labels import build_forward_return_labels


class ForwardReturnLabelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.event_dir = self.root / "events"
        self.event_dir.mkdir()
        self.events_path = self.event_dir / "candidate_transactions.csv"
        with self.events_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=(
                "event_id", "issuer_cik", "issuer_symbol", "transaction_code",
                "historical_available_after_utc"))
            writer.writeheader()
            writer.writerows([
                {"event_id": "before-close", "issuer_cik": "0000000001", "issuer_symbol": "AAPL",
                 "transaction_code": "S", "historical_available_after_utc": "2026-01-02T19:59:00Z"},
                {"event_id": "at-close", "issuer_cik": "0000000001", "issuer_symbol": "AAPL",
                 "transaction_code": "P", "historical_available_after_utc": "2026-01-02T20:00:00Z"},
                {"event_id": "old-event", "issuer_cik": "0000000001", "issuer_symbol": "AAPL",
                 "transaction_code": "S", "historical_available_after_utc": "2009-12-31T20:00:00Z"},
            ])
        event_raw = self.events_path.read_bytes()
        (self.event_dir / "manifest.json").write_text(json.dumps({"output": {
            "sha256": hashlib.sha256(event_raw).hexdigest(), "row_count": 3}}))
        self.prices_path = self.root / "prices.csv"
        with self.prices_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("symbol", "session_date", "close_at_utc", "adjusted_close"))
            writer.writeheader()
            for index, day in enumerate(("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
                                         "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13")):
                for symbol, start in (("AAPL", 100), ("SPY", 200)):
                    writer.writerow({"symbol": symbol, "session_date": day,
                                     "close_at_utc": f"{day}T20:00:00Z",
                                     "adjusted_close": start + index})

    def test_strict_close_cutoff_horizons_and_benchmark_excess(self):
        result = build_forward_return_labels(
            self.events_path, self.prices_path, self.root / "labels",
            price_source="test fixture", price_basis="split and dividend adjusted",
            prices_observed_at="2026-01-14T00:00:00Z", built_at_utc="2026-01-14T01:00:00Z")
        with result["csv_path"].open(encoding="utf-8", newline="") as stream:
            rows = {row["event_id"]: row for row in csv.DictReader(stream)}

        before = rows["before-close"]
        self.assertEqual(before["start_session_date"], "2026-01-02")
        self.assertEqual(before["end_session_date_1d"], "2026-01-05")
        self.assertEqual(before["forward_return_1d"], "0.010000000000")
        self.assertEqual(before["benchmark_return_1d"], "0.005000000000")
        self.assertEqual(before["excess_return_1d"], "0.005000000000")
        self.assertEqual(before["label_status_1d"], "OK")
        self.assertEqual(before["label_status_20d"], "INSUFFICIENT_FUTURE_ISSUER_PRICES")

        at_close = rows["at-close"]
        self.assertEqual(at_close["start_session_date"], "2026-01-05")
        self.assertEqual(at_close["primary_sample_eligible"], "true")
        self.assertEqual(rows["old-event"]["primary_sample_eligible"], "false")
        self.assertEqual(result["summary"]["events_with_start_close"], 3)

    def test_rejects_duplicate_price_sessions(self):
        with self.prices_path.open("a", encoding="utf-8", newline="") as stream:
            stream.write("AAPL,2026-01-02,2026-01-02T20:00:00Z,100\n")
        with self.assertRaisesRegex(ValueError, "Duplicate symbol/session"):
            build_forward_return_labels(
                self.events_path, self.prices_path, self.root / "labels",
                price_source="test fixture", price_basis="split and dividend adjusted",
                prices_observed_at="2026-01-14T00:00:00Z")

    def test_missing_benchmark_does_not_shift_issuer_start_session(self):
        with self.prices_path.open(encoding="utf-8", newline="") as stream:
            rows = [row for row in csv.DictReader(stream)
                    if not (row["symbol"] == "SPY" and row["session_date"] == "2026-01-02")]
        with self.prices_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("symbol", "session_date", "close_at_utc", "adjusted_close"))
            writer.writeheader()
            writer.writerows(rows)

        result = build_forward_return_labels(
            self.events_path, self.prices_path, self.root / "labels",
            price_source="test fixture", price_basis="split and dividend adjusted",
            prices_observed_at="2026-01-14T00:00:00Z", built_at_utc="2026-01-14T02:00:00Z")
        with result["csv_path"].open(encoding="utf-8", newline="") as stream:
            labels = {row["event_id"]: row for row in csv.DictReader(stream)}
        self.assertEqual(labels["before-close"]["start_session_date"], "2026-01-02")
        self.assertEqual(labels["before-close"]["label_status"], "MISSING_BENCHMARK_START_SESSION")
        self.assertEqual(labels["before-close"]["forward_return_1d"], "0.010000000000")
        self.assertIsNone(labels["before-close"]["benchmark_return_1d"] or None)
        self.assertEqual(labels["at-close"]["start_session_date"], "2026-01-05")

    def test_rejects_event_csv_hash_mismatch(self):
        self.events_path.write_text("event_id,issuer_cik\nchanged,0000000001\n")
        with self.assertRaisesRegex(ValueError, "Event CSV hash"):
            build_forward_return_labels(
                self.events_path, self.prices_path, self.root / "labels",
                price_source="test fixture", price_basis="split and dividend adjusted",
                prices_observed_at="2026-01-14T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
