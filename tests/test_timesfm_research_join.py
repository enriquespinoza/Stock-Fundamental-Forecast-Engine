import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from stock_analyzer.timesfm_research_join import join_timesfm_research_dataset


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TimesFMResearchJoinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.research_dir = self.root / "research"
        self.timesfm_dir = self.root / "timesfm"
        self.research_dir.mkdir()
        self.timesfm_dir.mkdir()
        self.research = self.research_dir / "event_research_dataset.csv"
        self.timesfm = self.timesfm_dir / "event_timesfm_features.csv"
        self.event_hash = "a" * 64
        research_rows = [
            {"event_id": "e1", "issuer_cik": "0000000001", "issuer_symbol": "AAA",
             "historical_available_after_utc": "2024-01-03T20:01:00Z", "label_forward_return_1d": "0.01"},
            {"event_id": "e2", "issuer_cik": "0000000002", "issuer_symbol": "BBB",
             "historical_available_after_utc": "2024-01-03T20:01:00Z", "label_forward_return_1d": "-0.02"},
        ]
        feature_rows = [self._feature("e2", "0000000002", "BBB", "OK", "2024-01-03T20:01:00Z"),
                        self._feature("e1", "0000000001", "AAA", "INSUFFICIENT_POINT_IN_TIME_CONTEXT", "2024-01-03T20:01:00Z")]
        self._write_csv(self.research, research_rows)
        self._write_csv(self.timesfm, feature_rows)
        (self.research_dir / "manifest.json").write_text(json.dumps({
            "schema_version": "form4-event-research-dataset-manifest-v1",
            "event_input": {"sha256": self.event_hash},
            "output": {"sha256": sha(self.research), "row_count": len(research_rows)},
        }), encoding="utf-8")
        self._write_timesfm_manifest(self.event_hash)

    @staticmethod
    def _feature(event_id, cik, symbol, status, available):
        row = {"event_id": event_id, "issuer_cik": cik, "issuer_symbol": symbol,
               "event_available_at_utc": available, "forecast_status": status,
               "forecast_origin_session_date": "2024-01-03" if status == "OK" else "",
               "forecast_origin_close_at_utc": "2024-01-03T20:00:00Z" if status == "OK" else "",
               "forecast_origin_adjusted_close": "100" if status == "OK" else "",
               "context_rows": "64" if status == "OK" else "12", "context_start_session_date": "2023-10-01" if status == "OK" else "",
               "context_end_session_date": "2024-01-03" if status == "OK" else "", "model_id": "google/timesfm-2.5-200m-pytorch"}
        for horizon in (1, 5, 20):
            row[f"forecast_adjusted_close_{horizon}s"] = str(100 + horizon) if status == "OK" else ""
            row[f"forecast_return_{horizon}s"] = str(horizon / 100) if status == "OK" else ""
        return row

    @staticmethod
    def _read_csv(path):
        with path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    @staticmethod
    def _write_csv(path, rows):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def _write_timesfm_manifest(self, event_hash):
        rows = self._read_csv(self.timesfm)
        (self.timesfm_dir / "manifest.json").write_text(json.dumps({
            "schema_version": "timesfm-event-features-manifest-v1",
            "input_events": {"sha256": event_hash},
            "model": {"id": "google/timesfm-2.5-200m-pytorch", "pretraining_cutoff_known": False},
            "point_in_time_policy": "close before event time",
            "research_only": True,
            "output": {"sha256": sha(self.timesfm), "row_count": len(rows)},
        }), encoding="utf-8")

    def test_joins_features_by_event_id_and_preserves_skipped_status(self):
        result = join_timesfm_research_dataset(self.research, self.timesfm, self.root / "output",
                                               built_at_utc="2024-02-01T00:00:00Z")
        rows = self._read_csv(result["csv_path"])
        self.assertEqual([row["event_id"] for row in rows], ["e1", "e2"])
        self.assertEqual(rows[0]["timesfm_forecast_status"], "INSUFFICIENT_POINT_IN_TIME_CONTEXT")
        self.assertEqual(rows[1]["timesfm_forecast_return_5s"], "0.05")
        self.assertEqual(result["summary"]["timesfm_forecast_status_counts"], {
            "INSUFFICIENT_POINT_IN_TIME_CONTEXT": 1, "OK": 1})
        manifest = json.loads((result["output_dir"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["output"]["sha256"], sha(result["csv_path"]))
        self.assertTrue(manifest["timesfm_features_input"]["research_only"])

    def test_rejects_features_from_different_event_csv(self):
        self._write_timesfm_manifest("b" * 64)
        with self.assertRaisesRegex(ValueError, "different event CSV"):
            join_timesfm_research_dataset(self.research, self.timesfm, self.root / "out")

    def test_rejects_issuer_or_timestamp_mismatch(self):
        rows = self._read_csv(self.timesfm)
        rows[0]["issuer_symbol"] = "WRONG"
        self._write_csv(self.timesfm, rows)
        self._write_timesfm_manifest(self.event_hash)
        with self.assertRaisesRegex(ValueError, "issuer does not match"):
            join_timesfm_research_dataset(self.research, self.timesfm, self.root / "out")

    def test_rejects_missing_or_extra_event_ids(self):
        rows = self._read_csv(self.timesfm)
        rows.pop()
        self._write_csv(self.timesfm, rows)
        self._write_timesfm_manifest(self.event_hash)
        with self.assertRaisesRegex(ValueError, "event IDs do not match"):
            join_timesfm_research_dataset(self.research, self.timesfm, self.root / "out")


if __name__ == "__main__":
    unittest.main()
