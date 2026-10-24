import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from stock_analyzer.sec_signal_diagnostics import diagnose_sec_signal_evaluation


class SecSignalDiagnosticsTests(unittest.TestCase):
    def test_reports_subgroup_performance_coverage_and_split_membership(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evaluation = root / "evaluation"
            research_dir = root / "research"
            out_root = root / "diagnostics"
            evaluation.mkdir()
            research_dir.mkdir()

            research_path = research_dir / "event_research_dataset.csv"
            research_fields = ["event_id", "issuer_cik", "issuer_symbol", "historical_available_after_utc",
                               "label_primary_sample_eligible", "label_label_status_5d",
                               "label_excess_return_5d", "label_start_session_date",
                               "label_end_session_date_5d",
                               "financial_revenue_10k_selection_status", "financial_assets_10k_selection_status",
                               "financial_revenue_10q_selection_status", "financial_assets_10q_selection_status"]
            research_rows = []
            for day in range(1, 11):
                date = f"2025-01-{day:02d}"
                research_rows.append({
                    "event_id": f"e{day}", "issuer_cik": "0000000001", "issuer_symbol": "AAA",
                    "historical_available_after_utc": f"{date}T00:00:00Z",
                    "label_primary_sample_eligible": "true", "label_label_status_5d": "OK",
                    "label_excess_return_5d": "0.1", "label_start_session_date": date,
                    "label_end_session_date_5d": f"2025-01-{day + 5:02d}" if day <= 3 else "2025-01-16",
                    "financial_revenue_10k_selection_status": "SELECTED_AS_OF_EVENT" if day == 9 else "NO_ELIGIBLE_FACT_AS_OF_EVENT",
                    "financial_assets_10k_selection_status": "NO_ELIGIBLE_FACT_AS_OF_EVENT",
                    "financial_revenue_10q_selection_status": "NO_ELIGIBLE_FACT_AS_OF_EVENT",
                    "financial_assets_10q_selection_status": "SELECTED_AS_OF_EVENT" if day == 10 else "NO_ELIGIBLE_FACT_AS_OF_EVENT",
                })
            with research_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=research_fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(research_rows)
            research_raw = research_path.read_bytes()
            (research_dir / "manifest.json").write_text(json.dumps({"output": {
                "sha256": hashlib.sha256(research_raw).hexdigest(), "row_count": len(research_rows)
            }}), encoding="utf-8")

            pred_fields = ["event_id", "issuer_cik", "issuer_symbol", "historical_available_after_utc",
                           "label_start_session_date", "label_end_session_date", "actual_excess_return",
                           "horizon_sessions", "holdout_cutoff_date"] + [f"prediction_{name}" for name in (
                               "price_only", "price_plus_insider", "price_plus_financial", "price_plus_10k",
                               "price_plus_10q", "price_plus_insider_10k", "price_plus_insider_10q", "price_plus_all_sec")]
            prediction_rows = []
            for day in (9, 10):
                date = f"2025-01-{day:02d}"
                prediction_row = {"event_id": f"e{day}", "issuer_cik": "0000000001", "issuer_symbol": "AAA",
                                  "historical_available_after_utc": f"{date}T00:00:00Z",
                                  "label_start_session_date": date, "label_end_session_date": "2025-01-16",
                                  "actual_excess_return": "0.1", "horizon_sessions": "5", "holdout_cutoff_date": "2025-01-09"}
                prediction_row.update({f"prediction_{name}": "0.08" for name in (
                    "price_only", "price_plus_insider", "price_plus_financial", "price_plus_10k", "price_plus_10q",
                    "price_plus_insider_10k", "price_plus_insider_10q", "price_plus_all_sec")})
                prediction_rows.append(prediction_row)
            prediction_path = evaluation / "sec_signal_holdout_predictions.csv"
            with prediction_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=pred_fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(prediction_rows)
            prediction_raw = prediction_path.read_bytes()
            (evaluation / "manifest.json").write_text(json.dumps({
                "outputs": {"predictions": {"sha256": hashlib.sha256(prediction_raw).hexdigest()}},
                "research_dataset": {"sha256": hashlib.sha256(research_raw).hexdigest()},
            }), encoding="utf-8")
            (evaluation / "summary.json").write_text(json.dumps({"model": {
                "holdout_fraction_by_unique_event_date": 0.2
            }}), encoding="utf-8")

            result = diagnose_sec_signal_evaluation(evaluation, research_path, out_root,
                                                    built_at_utc="2026-10-24T00:00:00Z")
            self.assertEqual(result["summary"]["unique_holdout_events"], 2)
            self.assertTrue(result["summary"]["split_checks"]["5"]["all_holdout_availability_on_or_after_cutoff"])
            self.assertTrue(result["summary"]["split_checks"]["5"]["holdout_ids_match_recomputed_chronological_split"])
            self.assertTrue(result["summary"]["split_checks"]["5"]["training_outcomes_end_before_first_holdout_start"])
            with (result["output_dir"] / "holdout_financial_coverage_by_issuer_year.csv").open(
                    encoding="utf-8", newline="") as stream:
                coverage = list(csv.DictReader(stream))
            ten_k = next(row for row in coverage if row["group_by"] == "issuer" and row["family"] == "10k")
            self.assertEqual(ten_k["selected_fact_slots"], "1")
            self.assertEqual(ten_k["possible_fact_slots"], "4")

if __name__ == "__main__":
    unittest.main()
