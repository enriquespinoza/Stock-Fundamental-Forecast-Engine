import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch


class Form4BatchTests(unittest.TestCase):
    def test_html_viewer_response_is_excluded_from_later_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "filing_ledger.jsonl"
            row = {"accession_number": "0000000001-24-000001", "form": "4",
                   "filing_date": "2024-02-26", "primary_document": "xslF345X05/ownership.xml"}
            ledger_path.write_text(json.dumps(row) + chr(10), encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "normalize_form4_batch.py"
            common = ["--ledger", str(ledger_path), "--limit", "10",
                      "--output-root", str(root / "normalized"),
                      "--summary-root", str(root / "summaries"),
                      "--user-agent", "Research Tool contact@example.com"]
            failed = type("Completed", (), {
                "stdout": "", "stderr": "UNSUPPORTED_SOURCE_FORMAT: SEC returned HTML viewer",
                "returncode": 1,
            })()
            with patch.object(sys, "argv", [str(script), *common]):
                with patch("subprocess.run", return_value=failed) as run:
                    with patch("time.sleep"):
                        with self.assertRaises(SystemExit) as exit_result:
                            runpy.run_path(str(script), run_name="__main__")
                        self.assertEqual(exit_result.exception.code, 0)
            run.assert_called_once()

            first_summary_path = next((root / "summaries").glob("*.json"))
            first_summary = json.loads(first_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(first_summary["success_count"], 0)
            self.assertEqual(first_summary["failure_count"], 0)
            self.assertEqual(first_summary["unsupported_count"], 1)

            with patch.object(sys, "argv", [str(script), *common]):
                with patch("subprocess.run") as retry:
                    with self.assertRaises(SystemExit) as exit_result:
                        runpy.run_path(str(script), run_name="__main__")
                    self.assertEqual(exit_result.exception.code, 0)
            retry.assert_not_called()

            succeeded = type("Completed", (), {"stdout": "normalized", "stderr": "", "returncode": 0})()
            with patch.object(sys, "argv", [str(script), *common, "--retry-unsupported"]):
                with patch("subprocess.run", return_value=succeeded) as retry:
                    with patch("time.sleep"):
                        with self.assertRaises(SystemExit) as exit_result:
                            runpy.run_path(str(script), run_name="__main__")
                        self.assertEqual(exit_result.exception.code, 0)
            retry.assert_called_once()
            self.assertIn("--download", retry.call_args.args[0])
            retried_summary = json.loads(sorted((root / "summaries").glob("*.json"))[-1].read_text(encoding="utf-8"))
            self.assertEqual(retried_summary["selected_count"], 1)
            self.assertEqual(retried_summary["success_count"], 1)

    def test_unsupported_legacy_html_is_reported_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "filing_ledger.jsonl"
            rows = [
                {"accession_number": "0000000001-03-000001", "form": "4",
                 "filing_date": "2003-01-01", "primary_document": "legacy.htm"},
                {"accession_number": "0000000001-26-000002", "form": "4",
                 "filing_date": "2026-01-01", "primary_document": "xslF345X02/form4.xml"},
            ]
            ledger_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "normalize_form4_batch.py"
            completed = type("Completed", (), {"stdout": "ok", "stderr": "", "returncode": 0})()
            with (
                patch.object(sys, "argv", [str(script), "--ledger", str(ledger_path), "--limit", "10",
                                           "--output-root", str(root / "normalized"),
                                           "--summary-root", str(root / "summaries"),
                                           "--user-agent", "Research Tool contact@example.com"]),
                patch("subprocess.run", return_value=completed) as run,
                patch("time.sleep"),
            ):
                with self.assertRaises(SystemExit) as exit_result:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(exit_result.exception.code, 0)

            run.assert_called_once()
            self.assertIn("0000000001-26-000002", run.call_args.args[0])
            summary_path = next((root / "summaries").glob("*.json"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["selected_count"], 1)
            self.assertEqual(summary["success_count"], 1)
            self.assertEqual(summary["failure_count"], 0)
            self.assertEqual(summary["unsupported_source_format"], [{
                "accession_number": "0000000001-03-000001", "filing_date": "2003-01-01",
                "form": "4", "primary_document": "legacy.htm", "status": "UNSUPPORTED_SOURCE_FORMAT"}])

    def test_start_date_is_inclusive_and_excludes_older_filings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "filing_ledger.jsonl"
            rows = [
                {"accession_number": "0000000001-09-000001", "form": "4",
                 "filing_date": "2009-12-31", "primary_document": "legacy.htm"},
                {"accession_number": "0000000001-10-000001", "form": "4",
                 "filing_date": "2010-01-01", "primary_document": "xslF345X02/form4.xml"},
                {"accession_number": "0000000001-26-000001", "form": "4/A",
                 "filing_date": "2026-01-01", "primary_document": "xslF345X02/form4.xml"},
            ]
            ledger_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "normalize_form4_batch.py"
            common = ["--ledger", str(ledger_path), "--limit", "10",
                      "--start-date", "2010-01-01",
                      "--output-root", str(root / "normalized"),
                      "--summary-root", str(root / "summaries"),
                      "--user-agent", "Research Tool contact@example.com"]
            completed = type("Completed", (), {"stdout": "ok", "stderr": "", "returncode": 0})()
            with (
                patch.object(sys, "argv", [str(script), *common]),
                patch("subprocess.run", return_value=completed) as run,
                patch("time.sleep"),
            ):
                with self.assertRaises(SystemExit) as exit_result:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(exit_result.exception.code, 0)

            self.assertEqual(run.call_count, 2)
            accessions = {call.args[0][call.args[0].index("--accession") + 1]
                          for call in run.call_args_list}
            self.assertEqual(accessions, {"0000000001-10-000001", "0000000001-26-000001"})
            summary_path = next((root / "summaries").glob("*.json"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["start_date"], "2010-01-01")
            self.assertEqual(summary["selected_count"], 2)
            self.assertEqual(summary["unsupported_source_format"], [])

    def test_issuer_mismatch_is_recorded_and_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "filing_ledger.jsonl"
            row = {
                "accession_number": "0000000001-26-000001",
                "form": "4",
                "filing_date": "2026-03-04",
                "issuer_cik": "0000093410",
                "source_url": "https://www.sec.gov/example",
                "primary_document": "xslF345X05/ownership.xml",
            }
            ledger_path.write_text(json.dumps(row) + "\\n", encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "normalize_form4_batch.py"
            argv = [
                str(script), "--ledger", str(ledger_path), "--limit", "10",
                "--start-date", "2010-01-01",
                "--output-root", str(root / "normalized"),
                "--summary-root", str(root / "summaries"),
                "--user-agent", "Research Tool contact@example.com",
            ]
            mismatch = type("Completed", (), {
                "stdout": "",
                "stderr": (
                    "ISSUER_CIK_MISMATCH: XML issuer CIK 0001789832 "
                    "does not match ledger CIK 0000093410"
                ),
                "returncode": 1,
            })()

            with patch.object(sys, "argv", argv), \\
                 patch("subprocess.run", return_value=mismatch) as run:
                with self.assertRaises(SystemExit) as exit_result:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(exit_result.exception.code, 0)

            run.assert_called_once()
            summary_path = next((root / "summaries").glob("*.json"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["success_count"], 0)
            self.assertEqual(summary["failure_count"], 0)
            self.assertEqual(summary["issuer_mismatch_exclusion_count"], 1)
            self.assertEqual(summary["issuer_mismatch_exclusions"][0]["xml_issuer_cik"], "0001789832")

            with patch.object(sys, "argv", argv), patch("subprocess.run") as retried:
                with self.assertRaises(SystemExit) as exit_result:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(exit_result.exception.code, 0)
            retried.assert_not_called()

    def test_invalid_start_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "filing_ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "normalize_form4_batch.py"
            with patch.object(sys, "argv", [str(script), "--ledger", str(ledger_path),
                                             "--start-date", "2010-13-01",
                                             "--user-agent", "Research Tool contact@example.com"]):
                with self.assertRaises(SystemExit) as exit_result:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(exit_result.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
