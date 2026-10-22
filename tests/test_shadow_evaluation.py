import csv
import hashlib
import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.shadow_evaluation import (
    _features, build_shadow_predictions, score_shadow_predictions, train_shadow_model_pair,
)


REVISION = "a" * 40


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def manifested(path, schema, rows, **manifest_values):
    write_csv(path, rows)
    manifest = {
        "schema_version": schema,
        "output": {"path": str(path), "sha256": digest(path), "row_count": len(rows)},
        **manifest_values,
    }
    (path.parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ShadowEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.research_root = self.root / "research"
        self.research_path = self.research_root / "event_research_dataset_with_timesfm.csv"
        self.actual_root = self.root / "actual"
        self.actual_path = self.actual_root / "event_timesfm_actual_returns.csv"
        self.tf_root = self.root / "timesfm"
        self.tf_path = self.tf_root / "event_timesfm_features.csv"
        self.tf_manifest = {
            "schema_version": "timesfm-event-features-manifest-v1",
            "model": {"id": "google/timesfm-2.5-200m-pytorch", "checkpoint_revision": REVISION},
        }
        self.tf_root.mkdir()
        self.tf_path.write_text("event_id\n", encoding="utf-8")
        (self.tf_root / "manifest.json").write_text(json.dumps(self.tf_manifest), encoding="utf-8")
        self.train_rows = []
        self.actual_rows = []
        for i in range(45):
            event_date = date(2024, 1, 1) + timedelta(days=i)
            event_id = f"train-{i}"
            event = self._event(event_id, event_date, 100 + i)
            self.train_rows.append(event)
            actual = self._actual(event, event_date + timedelta(days=21), return_base=(i % 5 - 2) / 100)
            self.actual_rows.append(actual)
        outcome_prices_path = self.root / "outcome-prices.csv"
        prices = []
        for i in range(121):
            day = date(2024, 1, 1) + timedelta(days=i)
            prices.append({"symbol": "AAA", "session_date": day.isoformat(),
                           "close_at_utc": f"{day.isoformat()}T20:00:00Z",
                           "adjusted_close": str(100 + i)})
        write_csv(outcome_prices_path, prices)
        outcome_price_manifest_path = self.root / "outcome-prices.manifest.json"
        outcome_price_manifest_path.write_text(json.dumps({
            "schema_version": "market-price-snapshot-manifest-v1",
            "observed_at_utc": "2024-12-30T00:00:00Z",
            "csv": {"sha256": digest(outcome_prices_path)},
        }), encoding="utf-8")
        actual_manifest = manifested(
            self.actual_path, "timesfm-actual-return-evaluation-manifest-v1", self.actual_rows,
            timesfm_features_input={"path": str(self.tf_path), "sha256": digest(self.tf_path),
                                    "manifest_sha256": digest(self.tf_root / "manifest.json")},
            price_input={"path": str(outcome_prices_path), "sha256": digest(outcome_prices_path),
                         "manifest_path": str(outcome_price_manifest_path),
                         "manifest_sha256": digest(outcome_price_manifest_path),
                         "observed_at_utc": "2024-12-30T00:00:00Z"},
        )
        research_manifest = manifested(
            self.research_path, "form4-event-research-timesfm-manifest-v1", self.train_rows,
            timesfm_features_input={"path": str(self.tf_path), "sha256": digest(self.tf_path),
                                    "manifest_sha256": digest(self.tf_root / "manifest.json")},
        )
        self.output_root = self.root / "models"

    def tearDown(self):
        self.temp.cleanup()

    def _event(self, event_id, when, price):
        available = datetime.combine(when, datetime.min.time(), timezone.utc) + timedelta(hours=21)
        origin = available - timedelta(hours=1)
        return {
            "event_id": event_id, "issuer_cik": "0000000001", "issuer_symbol": "AAA",
            "historical_available_after_utc": available.isoformat().replace("+00:00", "Z"),
            "timesfm_forecast_created_at_utc": (available + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "transaction_code": "S", "ownership_type": "D", "is_amendment": "false",
            "aff10b5one": "false", "shares": "100", "reported_gross_value": "1000",
            "reporting_owners_json": '[{"is_director":true,"is_officer":false,"is_ten_percent_owner":false}]',
            "timesfm_forecast_status": "OK", "timesfm_model_id": "google/timesfm-2.5-200m-pytorch",
            "timesfm_forecast_origin_session_date": when.isoformat(),
            "timesfm_forecast_origin_close_at_utc": origin.isoformat().replace("+00:00", "Z"),
            "timesfm_forecast_origin_adjusted_close": str(price),
            "timesfm_forecast_return_1s": "0.001", "timesfm_forecast_return_5s": "0.003",
            "timesfm_forecast_return_20s": "0.01",
            **{f"financial_{metric}_value": str(1000 + price) for metric in (
                "revenue", "net_income", "operating_income", "operating_cash_flow",
                "capital_expenditures", "assets", "liabilities", "equity", "cash",
                "shares_outstanding", "free_cash_flow")},
        }

    def _actual(self, event, end_date, return_base):
        return {
            "event_id": event["event_id"], "issuer_cik": event["issuer_cik"],
            "issuer_symbol": event["issuer_symbol"],
            "event_available_at_utc": event["historical_available_after_utc"],
            "forecast_status": "OK", "forecast_origin_session_date": event["timesfm_forecast_origin_session_date"],
            **{field: "OK" for h in (1, 5, 20) for field in (f"status_{h}s",)},
            **{f"actual_return_{h}s": str(return_base + h / 10000) for h in (1, 5, 20)},
            **{f"actual_end_session_date_{h}s": end_date.isoformat() for h in (1, 5, 20)},
        }

    def test_feature_allowlist_ignores_future_label_fields(self):
        event = self.train_rows[0]
        before = _features(event, include_timesfm=True)
        event["label_forward_return_20d"] = "999"
        event["actual_return_20s"] = "-999"
        self.assertEqual(before, _features(event, include_timesfm=True))

    def test_scorer_uses_only_matured_outcomes_and_clusters_paired_errors(self):
        predictions_dir = self.root / "logged"
        predictions_path = predictions_dir / "paired_shadow_predictions.csv"
        origins = [date(2024, 1, 1), date(2024, 1, 15)]
        predictions = []
        actuals = []
        price_rows = []
        all_dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(70)]
        for symbol, cik in (("AAA", "0000000001"), ("BBB", "0000000002")):
            for index, day in enumerate(all_dates):
                price_rows.append({
                    "symbol": symbol, "session_date": day.isoformat(),
                    "close_at_utc": f"{day.isoformat()}T20:00:00Z",
                    "adjusted_close": str(100 + index),
                })
        for index, (symbol, cik, origin) in enumerate((
            ("AAA", "0000000001", origins[0]), ("BBB", "0000000002", origins[1]),
        )):
            event_id = f"score-{index}"
            available = datetime.combine(origin, datetime.min.time(), timezone.utc) + timedelta(hours=22)
            predictions.append({
                "event_id": event_id, "issuer_cik": cik, "issuer_symbol": symbol,
                "event_available_at_utc": available.isoformat().replace("+00:00", "Z"),
                "forecast_created_at_utc": (available + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "forecast_origin_session_date": origin.isoformat(),
                "forecast_origin_close_at_utc": f"{origin.isoformat()}T20:00:00Z",
                "forecast_origin_adjusted_close": "100",
                **{f"sec_baseline_pred_return_{h}s": "0" for h in (1, 5, 20)},
                **{f"sec_timesfm_pred_return_{h}s": "0.01" for h in (1, 5, 20)},
            })
            actuals.append({
                "event_id": event_id, "issuer_cik": cik, "issuer_symbol": symbol,
                "event_available_at_utc": available.isoformat().replace("+00:00", "Z"),
                "forecast_status": "OK", "forecast_origin_session_date": origin.isoformat(),
                **{f"status_{h}s": "OK" for h in (1, 5, 20)},
                **{f"actual_return_{h}s": "0.02" for h in (1, 5, 20)},
                **{f"actual_end_session_date_{h}s": (origin + timedelta(days=h)).isoformat()
                   for h in (1, 5, 20)},
            })
        pred_manifest = manifested(
            predictions_path, "paired-shadow-predictions-manifest-v1", predictions,
            research_only=True,
        )
        # Rename schema output correctly and retain the manifest hash check.
        price_path = self.root / "score-prices.csv"
        write_csv(price_path, price_rows)
        price_manifest_path = self.root / "score-prices.manifest.json"
        price_manifest_path.write_text(json.dumps({
            "schema_version": "market-price-snapshot-manifest-v1",
            "observed_at_utc": "2024-03-15T00:00:00Z",
            "csv": {"sha256": digest(price_path)},
        }), encoding="utf-8")
        actual_path = self.root / "score-actual" / "actual.csv"
        manifested(
            actual_path, "timesfm-actual-return-evaluation-manifest-v1", actuals,
            price_input={"sha256": digest(price_path),
                         "manifest_sha256": digest(price_manifest_path),
                         "observed_at_utc": "2024-03-15T00:00:00Z"},
        )
        result = score_shadow_predictions(
            predictions_path, actual_path, price_path, price_manifest_path,
            self.root / "scores", replicates=100, scored_at_utc="2024-01-10T00:00:00Z",
        )
        metrics = result["summary"]["metrics"]
        self.assertEqual(metrics["1s"]["matched_count"], 1)
        self.assertLess(metrics["1s"]["mae_delta_augmented_minus_baseline"], 0)
        self.assertEqual(result["summary"]["matured_status_counts"]["20s"]["OUTCOME_NOT_YET_MATURED"], 1)
        self.assertEqual(result["summary"]["paired_mae_delta_95pct_cluster_ci"]["1s"]["cluster_count"], 1)
        self.assertEqual(result["summary"]["predictions_created_after_score_time"], 1)

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "install the optional research extra")
    def test_training_uses_only_post_checkpoint_features_and_prediction_is_frozen_paired(self):
        self.train_rows[0]["timesfm_forecast_created_at_utc"] = "2024-01-22T20:00:00Z"
        manifested(
            self.research_path, "form4-event-research-timesfm-manifest-v1", self.train_rows,
            timesfm_features_input={"path": str(self.tf_path), "sha256": digest(self.tf_path),
                                    "manifest_sha256": digest(self.tf_root / "manifest.json")},
        )
        result = train_shadow_model_pair(
            self.research_path, self.actual_path, self.output_root,
            training_cutoff_utc="2024-12-31T00:00:00Z",
            timesfm_feature_start_utc="2024-01-01T00:00:00Z",
            code_revision="b" * 40,
            frozen_at_utc="2025-01-01T00:00:00Z",
            minimum_training_rows=10,
            built_at_utc="2025-01-01T00:00:01Z",
        )
        self.assertEqual(result["summary"]["horizon_training"]["20s"]["training_row_count"], 44)
        self.assertEqual(result["summary"]["horizon_training"]["20s"]["excluded_features_created_after_target"], 1)
        self.assertEqual(result["manifest"]["timesfm_checkpoint"]["checkpoint_revision"], REVISION)

        # Construct a new, post-freeze event and a pinned feature/price snapshot.
        future_date = date(2025, 1, 10)
        event = self._event("future-1", future_date, 200)
        future_path = self.root / "future" / "future.csv"
        event["historical_available_after_utc"] = "2025-01-10T21:01:00Z"
        event["timesfm_forecast_origin_close_at_utc"] = "2025-01-10T20:00:00Z"
        feature_root = self.root / "future-timesfm"
        feature_root.mkdir()
        future_prices = []
        for index in range(1, 61):
            day = date(2025, 1, 1) + timedelta(days=index)
            future_prices.append({"symbol": "AAA", "session_date": day.isoformat(),
                                  "close_at_utc": f"{day.isoformat()}T20:00:00Z",
                                  "adjusted_close": str(100 + index)})
        prices_path = feature_root / "prices.csv"
        write_csv(prices_path, future_prices)
        price_manifest_path = feature_root / "prices.manifest.json"
        price_manifest_path.write_text(json.dumps({
            "schema_version": "market-price-snapshot-manifest-v1",
            "observed_at_utc": "2025-03-10T00:00:00Z",
            "csv": {"sha256": digest(prices_path)},
        }), encoding="utf-8")
        tf_path = feature_root / "event_timesfm_features.csv"
        tf_path.write_text("event_id\nfuture-1\n", encoding="utf-8")
        feature_manifest = {
            "schema_version": "timesfm-event-features-manifest-v1",
            "model": {"id": "google/timesfm-2.5-200m-pytorch", "checkpoint_revision": REVISION},
            "input_prices": {"path": str(prices_path), "sha256": digest(prices_path),
                             "manifest_path": str(price_manifest_path),
                             "manifest_sha256": digest(price_manifest_path)},
        }
        (feature_root / "manifest.json").write_text(json.dumps(feature_manifest), encoding="utf-8")
        event["timesfm_forecast_origin_session_date"] = "2025-01-10"
        event["timesfm_forecast_origin_adjusted_close"] = "110"
        future_path.parent.mkdir()
        manifested(future_path, "form4-event-research-timesfm-manifest-v1", [event],
                   timesfm_features_input={"path": str(tf_path), "sha256": digest(tf_path),
                                           "manifest_sha256": digest(feature_root / "manifest.json")})
        prediction_result = build_shadow_predictions(
            future_path, result["output_dir"], self.root / "predictions",
            built_at_utc="2025-01-11T00:00:00Z",
        )
        with prediction_result["csv_path"].open(encoding="utf-8", newline="") as stream:
            prediction = next(csv.DictReader(stream))
        self.assertEqual(prediction["event_id"], "future-1")
        self.assertTrue(prediction["sec_baseline_pred_return_20s"])
        self.assertTrue(prediction["sec_timesfm_pred_return_20s"])
        self.assertTrue(prediction_result["manifest"]["research_only"])


if __name__ == "__main__":
    unittest.main()
