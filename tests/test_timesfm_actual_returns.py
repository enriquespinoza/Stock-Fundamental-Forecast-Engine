import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.timesfm_actual_returns import build_timesfm_actual_returns


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TimesFMActualReturnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.price_path = self.root / "prices.csv"
        self.price_manifest_path = self.root / "prices.manifest.json"
        with self.price_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["symbol", "session_date", "close_at_utc", "adjusted_close"])
            for i in range(1, 24):
                date = f"2024-01-{i:02d}"
                close_at = f"{date}T20:00:00Z"
                writer.writerow(["AAA", date, close_at, 100 + i])
                writer.writerow(["SPY", date, close_at, 400 + i])
        self.price_manifest_path.write_text(json.dumps({
            "schema_version": "market-price-snapshot-manifest-v1",
            "observed_at_utc": "2024-02-01T00:00:00Z",
            "row_count": 46,
            "csv": {"sha256": digest(self.price_path)},
        }), encoding="utf-8")
        self.feature_dir = self.root / "features"
        self.feature_dir.mkdir()
        self.features_path = self.feature_dir / "event_timesfm_features.csv"
        fields = ["event_id", "issuer_cik", "issuer_symbol", "event_available_at_utc",
                  "forecast_status", "forecast_origin_session_date", "forecast_origin_close_at_utc",
                  "forecast_origin_adjusted_close", "forecast_return_1s", "forecast_return_5s",
                  "forecast_return_20s"]
        with self.features_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerow({"event_id": "e1", "issuer_cik": "0000000001", "issuer_symbol": "AAA",
                             "event_available_at_utc": "2024-01-03T20:01:00Z", "forecast_status": "OK",
                             "forecast_origin_session_date": "2024-01-03",
                             "forecast_origin_close_at_utc": "2024-01-03T20:00:00Z",
                             "forecast_origin_adjusted_close": "103",
                             "forecast_return_1s": "0.01", "forecast_return_5s": "0.05",
                             "forecast_return_20s": "0.20"})
            writer.writerow({"event_id": "e2", "issuer_cik": "0000000001", "issuer_symbol": "AAA",
                             "event_available_at_utc": "2009-01-01T00:00:00Z",
                             "forecast_status": "INSUFFICIENT_POINT_IN_TIME_CONTEXT",
                             "forecast_origin_session_date": "", "forecast_origin_close_at_utc": "",
                             "forecast_origin_adjusted_close": "", "forecast_return_1s": "",
                             "forecast_return_5s": "", "forecast_return_20s": ""})
        self.feature_manifest_path = self.feature_dir / "manifest.json"
        self.feature_manifest_path.write_text(json.dumps({
            "schema_version": "timesfm-event-features-manifest-v1",
            "output": {"sha256": digest(self.features_path), "row_count": 2},
            "input_prices": {"path": str(self.price_path),
                             "sha256": digest(self.price_path),
                             "manifest_path": str(self.price_manifest_path),
                             "manifest_sha256": digest(self.price_manifest_path)},
        }), encoding="utf-8")
        self.output_root = self.root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        return build_timesfm_actual_returns(self.features_path, self.price_path,
                                            self.price_manifest_path, self.output_root,
                                            built_at_utc="2024-02-02T00:00:00Z")

    def test_realized_returns_use_timesfm_origin_and_exact_session_horizons(self):
        result = self.build()
        with result["csv_path"].open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        row = rows[0]
        self.assertEqual(row["forecast_origin_session_date"], "2024-01-03")
        self.assertEqual(row["actual_end_session_date_1s"], "2024-01-04")
        self.assertAlmostEqual(float(row["actual_return_1s"]), 104 / 103 - 1)
        self.assertEqual(row["actual_end_session_date_5s"], "2024-01-08")
        self.assertAlmostEqual(float(row["actual_return_5s"]), 108 / 103 - 1)
        self.assertEqual(row["actual_end_session_date_20s"], "2024-01-23")
        self.assertEqual(row["status_20s"], "OK")
        self.assertEqual(result["summary"]["primary_sample_metrics"]["1s"]["matched_count"], 1)
        self.assertIn("zero_return_baseline_mae", result["summary"]["primary_sample_metrics"]["1s"])

    def test_insufficient_future_prices_are_reported_per_horizon(self):
        with self.features_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["event_available_at_utc"] = "2024-01-22T20:01:00Z"
        rows[0]["forecast_origin_session_date"] = "2024-01-22"
        rows[0]["forecast_origin_close_at_utc"] = "2024-01-22T20:00:00Z"
        rows[0]["forecast_origin_adjusted_close"] = "122"
        with self.features_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        self.feature_manifest_path.write_text(json.dumps({
            "schema_version": "timesfm-event-features-manifest-v1",
            "output": {"sha256": digest(self.features_path), "row_count": 2},
            "input_prices": {"path": str(self.price_path), "sha256": digest(self.price_path),
                             "manifest_path": str(self.price_manifest_path),
                             "manifest_sha256": digest(self.price_manifest_path)},
        }), encoding="utf-8")
        result = self.build()
        with result["csv_path"].open(encoding="utf-8", newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["status_1s"], "OK")
        self.assertEqual(row["status_5s"], "INSUFFICIENT_FUTURE_ISSUER_PRICES")
        self.assertEqual(row["status_20s"], "INSUFFICIENT_FUTURE_ISSUER_PRICES")

    def test_modified_price_snapshot_fails_hash_validation(self):
        self.price_path.write_text(self.price_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Price CSV hash"):
            self.build()

    def test_origin_at_or_after_public_event_time_is_rejected(self):
        with self.features_path.open(encoding="utf-8", newline="") as stream:
            features = list(csv.DictReader(stream))
        features[0]["event_available_at_utc"] = "2024-01-03T20:00:00Z"
        with self.features_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=features[0].keys(), lineterminator="\n")
            writer.writeheader()
            writer.writerows(features)
        self.feature_manifest_path.write_text(json.dumps({
            "schema_version": "timesfm-event-features-manifest-v1",
            "output": {"sha256": digest(self.features_path), "row_count": 2},
            "input_prices": {"path": str(self.price_path), "sha256": digest(self.price_path),
                             "manifest_path": str(self.price_manifest_path),
                             "manifest_sha256": digest(self.price_manifest_path)},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "strictly before event"):
            self.build()


if __name__ == "__main__":
    unittest.main()
