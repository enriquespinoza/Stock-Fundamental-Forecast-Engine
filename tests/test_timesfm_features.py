import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.timesfm_features import build_timesfm_features


class FakeForecaster:
    def __init__(self):
        self.calls = []

    def forecast(self, values):
        self.calls.append(list(values))
        return [values[-1] * (1 + h / 100) for h in range(1, 21)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TimesFMFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.event_dir = self.root / "events"
        self.event_dir.mkdir()
        self.events = self.event_dir / "candidate_transactions.csv"
        with self.events.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["event_id", "issuer_cik", "issuer_symbol", "historical_available_after_utc"])
            writer.writeheader()
            writer.writerows([
                {"event_id": "e1", "issuer_cik": "0000000001", "issuer_symbol": "AAA", "historical_available_after_utc": "2024-01-03T20:01:00Z"},
                {"event_id": "e2", "issuer_cik": "0000000001", "issuer_symbol": "AAA", "historical_available_after_utc": "2024-01-03T20:30:00Z"},
                {"event_id": "e3", "issuer_cik": "0000000001", "issuer_symbol": "BBB", "historical_available_after_utc": "2024-01-03T20:01:00Z"},
            ])
        self.event_manifest = self.event_dir / "manifest.json"
        self.event_manifest.write_text(json.dumps({"schema_version": "form4-event-dataset-manifest-v1", "output": {"sha256": digest(self.events), "row_count": 3}}), encoding="utf-8")
        self.prices = self.root / "prices.csv"
        with self.prices.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["symbol", "session_date", "close_at_utc", "adjusted_close"])
            for symbol in ("AAA", "BBB"):
                for day, price in [(1, 100), (2, 101), (3, 102), (4, 999)]:
                    writer.writerow([symbol, f"2024-01-0{day}", f"2024-01-0{day}T20:00:00Z", price])
        self.price_manifest = self.root / "prices.manifest.json"
        self.price_manifest.write_text(json.dumps({"schema_version": "market-price-snapshot-manifest-v1", "observed_at_utc": "2024-01-10T00:00:00Z", "row_count": 8, "csv": {"sha256": digest(self.prices)}}), encoding="utf-8")
        self.output = self.root / "output"

    def tearDown(self):
        self.temp.cleanup()

    def test_forecasts_only_use_closes_known_at_event_and_outputs_provenance(self):
        model = FakeForecaster()
        result = build_timesfm_features(self.events, self.prices, self.price_manifest, self.output,
            forecaster=model, min_context=2, context_length=10, built_at_utc="2024-01-11T00:00:00Z")
        with result["csv_path"].open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["forecast_origin_session_date"] for row in rows], ["2024-01-03", "2024-01-03", "2024-01-03"])
        self.assertTrue(all(row["forecast_status"] == "OK" for row in rows))
        self.assertTrue(all(row["forecast_generated_at_utc"].startswith("2024-01-11T00:00:00") for row in rows))
        self.assertEqual(len(model.calls), 2)  # shared event context is cached
        self.assertNotIn(999, model.calls[0])
        self.assertAlmostEqual(float(rows[0]["forecast_return_5s"]), 0.05)
        manifest = json.loads((result["output_dir"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["research_only"])
        self.assertFalse(manifest["model"]["pretraining_cutoff_known"])
        self.assertEqual(manifest["output"]["sha256"], digest(result["csv_path"]))

    def test_insufficient_context_is_explicit_and_does_not_call_model(self):
        model = FakeForecaster()
        result = build_timesfm_features(self.events, self.prices, self.price_manifest, self.output,
            forecaster=model, min_context=4, context_length=10)
        with result["csv_path"].open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["forecast_status"] for row in rows], ["INSUFFICIENT_POINT_IN_TIME_CONTEXT"] * 3)
        self.assertEqual(model.calls, [])

    def test_price_hash_mismatch_fails_closed(self):
        self.price_manifest.write_text(json.dumps({"schema_version": "market-price-snapshot-manifest-v1", "observed_at_utc": "2024-01-10T00:00:00Z", "row_count": 8, "csv": {"sha256": "0" * 64}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Price CSV hash"):
            build_timesfm_features(self.events, self.prices, self.price_manifest, self.output, forecaster=FakeForecaster())

    def test_snapshot_observed_before_event_is_not_used_as_point_in_time_data(self):
        self.price_manifest.write_text(json.dumps({"schema_version": "market-price-snapshot-manifest-v1", "observed_at_utc": "2024-01-02T00:00:00Z", "row_count": 8, "csv": {"sha256": digest(self.prices)}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot observation precedes event"):
            build_timesfm_features(self.events, self.prices, self.price_manifest, self.output, forecaster=FakeForecaster(), min_context=2)


if __name__ == "__main__":
    unittest.main()
