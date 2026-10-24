import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import date, timedelta

from stock_analyzer.sec_signal_models import (
    _event_features, _price_features, _split_rows, evaluate_sec_signal_models,
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class SecSignalModelTests(unittest.TestCase):
    def test_price_features_exclude_close_at_or_after_event_timestamp(self):
        history = [
            {"session_date": f"2020-01-{day:02d}", "close_at": __import__("datetime").datetime(
                2020, 1, day, 21, 0, tzinfo=__import__("datetime").timezone.utc),
             "close": float(day)}
            for day in range(1, 6)
        ]
        event = {"issuer_symbol": "AAA", "historical_available_after_utc": "2020-01-04T21:00:00Z"}
        features = _price_features(event, {"AAA": history, "SPY": history}, "SPY")
        self.assertAlmostEqual(features["price.issuer.return_1s"], 3 / 2 - 1)

    def test_future_financial_fact_is_rejected(self):
        row = {
            "financial_assets_selection_status": "SELECTED_AS_OF_EVENT",
            "financial_assets_fact_available_after_utc": "2021-01-02T00:00:00Z",
            "financial_assets_value": "100",
        }
        with self.assertRaisesRegex(ValueError, "not available"):
            _event_features(row, ["financial_assets_value"], __import__("datetime").datetime.fromisoformat(
                "2021-01-01T00:00:00+00:00"))

    def test_constant_predictions_have_undefined_spearman_without_warning(self):
        from stock_analyzer.sec_signal_models import _metrics
        result = _metrics([0.01, -0.02, 0.03], [0.0, 0.0, 0.0])
        self.assertIsNone(result["spearman_ic"])

    def test_chronological_split_purges_training_outcomes_overlapping_holdout(self):
        rows = []
        start = date(2020, 1, 1)
        for index in range(40):
            event_day = start + timedelta(days=index)
            start_session = event_day + timedelta(days=1)
            rows.append({
                "historical_available_after_utc": f"{event_day.isoformat()}T12:00:00Z",
                "label_primary_sample_eligible": "true",
                "label_label_status_5d": "OK",
                "label_excess_return_5d": "0.01",
                "label_start_session_date": start_session.isoformat(),
                "label_end_session_date_5d": (start_session + timedelta(days=5)).isoformat(),
            })
        train, test, cutoff = _split_rows(
            rows, 5, train_fraction=0.75, minimum_training_rows=3, minimum_test_rows=2)
        first_test_start = min(rows[index]["label_start_session_date"] for index in test)
        self.assertTrue(all(rows[index]["label_end_session_date_5d"] < first_test_start for index in train))
        self.assertTrue(all(rows[index]["historical_available_after_utc"][:10] >= cutoff for index in test))

    def test_evaluation_writes_hash_backed_holdout_predictions(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("research extra is not installed")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prices_path = root / "prices.csv"
            price_manifest_path = root / "prices.manifest.json"
            price_rows = []
            first_day = date(2020, 1, 1)
            for index in range(150):
                day = first_day + timedelta(days=index)
                close_time = f"{day.isoformat()}T21:00:00Z"
                for symbol, base in (("AAA", 100.0), ("SPY", 200.0)):
                    price_rows.append([symbol, day.isoformat(), close_time, f"{base + index * 0.1:.4f}"])
            with prices_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(["symbol", "session_date", "close_at_utc", "adjusted_close"])
                writer.writerows(price_rows)
            price_raw = prices_path.read_bytes()
            price_manifest_path.write_text(json.dumps({
                "schema_version": "market-price-snapshot-manifest-v1",
                "observed_at_utc": "2021-01-01T00:00:00Z",
                "row_count": len(price_rows),
                "csv": {"path": str(prices_path), "sha256": sha(price_raw)},
            }), encoding="utf-8")

            labels_path = root / "labels" / "event_return_labels.csv"
            labels_path.parent.mkdir()
            labels_content = b"event_id,label_status_5d\n"
            labels_path.write_bytes(labels_content)
            (labels_path.parent / "manifest.json").write_text(json.dumps({
                "output": {"sha256": sha(labels_content)},
                "price_input": {"sha256": sha(price_raw)},
            }), encoding="utf-8")

            dataset_path = root / "research" / "event_research_dataset.csv"
            dataset_path.parent.mkdir()
            rows = []
            for index in range(120):
                event_day = first_day + timedelta(days=index)
                start_i = index + 1
                end5_i = start_i + 5
                end20_i = start_i + 20
                rows.append({
                    "event_id": f"event-{index}", "issuer_cik": "0000000001", "issuer_symbol": "AAA",
                    "historical_available_after_utc": f"{event_day.isoformat()}T12:00:00Z",
                    "transaction_code": "P" if index % 2 else "S", "shares": str(100 + index),
                    "financial_assets_value": str(1000000 + index * 2000),
                    "financial_assets_selection_status": "SELECTED_AS_OF_EVENT",
                    "financial_assets_fact_available_after_utc": f"{event_day.isoformat()}T11:00:00Z",
                    "financial_assets_unit": "USD", "label_primary_sample_eligible": "true",
                    "label_event_available_at_utc": f"{event_day.isoformat()}T12:00:00Z",
                    "label_benchmark_symbol": "SPY", "label_label_status_5d": "OK",
                    "label_label_status_20d": "OK", "label_excess_return_5d": f"{(index % 11 - 5) / 1000:.6f}",
                    "label_excess_return_20d": f"{(index % 13 - 6) / 500:.6f}",
                    "label_start_session_date": (first_day + timedelta(days=start_i)).isoformat(),
                    "label_end_session_date_5d": (first_day + timedelta(days=end5_i)).isoformat(),
                    "label_end_session_date_20d": (first_day + timedelta(days=end20_i)).isoformat(),
                })
            with dataset_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            dataset_raw = dataset_path.read_bytes()
            (dataset_path.parent / "manifest.json").write_text(json.dumps({
                "forecast_status": "LABELS_AND_FEATURES_NO_FORECAST",
                "return_labels_input": {"path": str(labels_path), "sha256": sha(labels_content)},
                "event_input": {"sha256": "eventhash"},
                "output": {"sha256": sha(dataset_raw), "row_count": len(rows)},
            }), encoding="utf-8")

            result = evaluate_sec_signal_models(
                dataset_path, prices_path, price_manifest_path, root / "evaluations",
                minimum_training_rows=30, minimum_test_rows=15, bootstrap_replicates=50,
                built_at_utc="2021-01-01T00:00:00Z",
            )
            output = result["output_dir"]
            prediction_path = output / "sec_signal_holdout_predictions.csv"
            with prediction_path.open(encoding="utf-8", newline="") as stream:
                predictions = list(csv.DictReader(stream))
            self.assertTrue(predictions)
            self.assertIn("prediction_price_only", predictions[0])
            self.assertIn("prediction_price_plus_all_sec", predictions[0])
            self.assertEqual(result["summary"]["forecast_status"], "RESEARCH_ONLY_SELECTED_COHORT_EXPLORATORY")
            self.assertIn("mae_delta_vs_price_only", result["summary"]["horizons"]["5s"]["metrics"]["price_plus_all_sec"])
            self.assertTrue((output / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
