import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch


class Form4BatchTests(unittest.TestCase):
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
            with patch.object(sys, "argv", [str(script), "--ledger", str(ledger_path), "--limit", "10",
                                             "--output-root", str(root / "normalized"),
                                             "--summary-root", str(root / "summaries"),
                                             "--user-agent", "Research Tool contact@example.com"]), \
                 patch("subprocess.run", return_value=completed) as run, \
                 patch("time.sleep"):
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


if __name__ == "__main__":
    unittest.main()
