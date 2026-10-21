from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import runpy
import sys
from urllib.error import HTTPError

from stock_analyzer.ledger import build_ledger, eligible_as_of, normalize_cik, parse_timestamp
from stock_analyzer.sec_client import SecClient, UnsupportedSourceFormat


def page(accepted="2026-10-09T20:30:00Z", accession="0000000001-26-000001", form="10-Q"):
    return {"accessionNumber": [accession], "form": [form], "filingDate": ["2026-10-09"],
            "reportDate": ["2026-06-30"], "primaryDocument": ["report.htm"],
            "acceptanceDateTime": [accepted]}


class LedgerTests(unittest.TestCase):
    def ledger(self, *pages):
        return build_ledger("1", list(pages), observed_at="2026-10-12T12:00:00Z")

    def test_report_date_does_not_create_early_availability(self):
        rows = self.ledger(page())
        self.assertEqual(eligible_as_of(rows, "2026-07-01T00:00:00Z"), [])
        self.assertEqual(eligible_as_of(rows, "2026-10-09T20:30:59Z"), [])
        self.assertEqual(len(eligible_as_of(rows, "2026-10-09T20:31:00Z")), 1)

    def test_live_availability_respects_observation(self):
        rows = self.ledger(page())
        self.assertEqual(eligible_as_of(rows, "2026-10-10T00:00:00Z", mode="live"), [])
        self.assertEqual(len(eligible_as_of(rows, "2026-10-12T12:00:00Z", mode="live")), 1)

    def test_missing_acceptance_is_excluded(self):
        rows = self.ledger(page(None))
        self.assertEqual(rows[0]["availability_status"], "UNKNOWN_ACCEPTANCE_TIME")
        self.assertEqual(eligible_as_of(rows, "2027-01-01T00:00:00Z"), [])

    def test_amendment_preserves_original(self):
        rows = self.ledger(page(), page("2026-10-12T10:00:00Z", "0000000001-26-000002", "10-Q/A"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["is_amendment"])
        self.assertEqual(len(eligible_as_of(rows, "2026-10-10T00:00:00Z")), 1)

    def test_duplicate_is_idempotent_but_conflicts_fail(self):
        self.assertEqual(len(self.ledger(page(), page())), 1)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.ledger(page(), page(form="10-K"))

    def test_invalid_columns_and_future_times_fail(self):
        malformed = page()
        malformed["reportDate"] = []
        with self.assertRaises(ValueError):
            self.ledger(malformed)
        with self.assertRaises(ValueError):
            self.ledger(page("2027-01-01T00:00:00Z"))

    def test_issuer_mismatch_fails(self):
        with self.assertRaises(ValueError):
            self.ledger({"cik": 2, "filings": {"recent": page()}})

    def test_timezone_and_identifiers(self):
        self.assertEqual(normalize_cik("320193"), "0000320193")
        self.assertEqual(parse_timestamp("2026-10-09T16:30:00-04:00").hour, 20)
        for cik in ("0", "../1", "12345678901"):
            with self.assertRaises(ValueError):
                normalize_cik(cik)
        with self.assertRaises(ValueError):
            parse_timestamp("2026-10-09T16:30:00")
        with self.assertRaises(ValueError):
            self.ledger(page("2026-10-09T16:30:00"))


class ClientTests(unittest.TestCase):
    url = "https://data.sec.gov/submissions/CIK0000000001.json"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.clock_value = 0.0
        self.sleeps = []

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.clock_value += delay

    def client(self, opener, **kwargs):
        return SecClient("StockResearch test@example.com", self.directory, opener=opener,
                         clock=lambda: self.clock_value, sleep=self.sleep,
                         now=lambda: datetime(2026, 10, 12, 12, tzinfo=timezone.utc), **kwargs)

    def test_cache_refresh_preserves_old_bytes_and_observations(self):
        responses = iter([b'{"value": 1}', b'{"value": 2}'])
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return BytesIO(next(responses))
        client = self.client(opener)
        first = client.fetch_json(self.url)
        cached = client.fetch_json(self.url)
        refreshed = client.fetch_json(self.url, refresh=True)
        self.assertTrue(cached.from_cache)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first.sha256, refreshed.sha256)
        self.assertEqual(json.loads(first.path.read_bytes()), {"value": 1})
        self.assertEqual(len(list(first.path.parent.glob("observation-*.json"))), 2)
        self.assertEqual(self.sleeps, [0.5])

    def test_cache_tampering_fails(self):
        client = self.client(lambda request, timeout: BytesIO(b'{}'))
        snapshot = client.fetch_json(self.url)
        snapshot.path.write_bytes(b'{"tampered": true}')
        with self.assertRaisesRegex(ValueError, "checksum"):
            client.fetch_json(self.url)

    def test_429_retry_after(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(self.url, 429, "limited", {"Retry-After": "3"}, None)
            return BytesIO(b'{}')
        self.client(opener).fetch_json(self.url)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleeps, [3])

    def test_403_is_not_retried(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise HTTPError(self.url, 403, "forbidden", {}, None)
        with self.assertRaises(HTTPError):
            self.client(opener).fetch_json(self.url)
        self.assertEqual(len(calls), 1)

    def test_bad_identity_and_url_fail(self):
        with self.assertRaises(ValueError):
            SecClient("anonymous", self.directory)
        with self.assertRaises(ValueError):
            self.client(lambda request, timeout: BytesIO(b'{}')).fetch_json("https://example.com/data")

    def test_html_viewer_is_reported_as_unsupported_source(self):
        url = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/xslF345X05/ownership.xml"
        html = b'<!DOCTYPE html><html><body>SEC Form 4</body></html>'
        client = self.client(lambda request, timeout: BytesIO(html))
        with self.assertRaisesRegex(UnsupportedSourceFormat, "UNSUPPORTED_SOURCE_FORMAT"):
            client.fetch_xml(url)

    def test_retry_exhaustion_and_invalid_json(self):
        calls = []
        def unavailable(request, timeout):
            calls.append(request)
            raise HTTPError(self.url, 503, "unavailable", {}, None)
        with self.assertRaises(HTTPError):
            self.client(unavailable, retries=1).fetch_json(self.url)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(ValueError):
            self.client(lambda request, timeout: BytesIO(b'[]')).fetch_json(self.url)
        self.assertFalse(any(self.directory.glob("*/latest.json")))


class IngestionTests(unittest.TestCase):
    def test_cli_archives_pages_and_preserves_cached_observation_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = {"cik": 1, "filings": {"recent": page(), "files": [
                {"name": "CIK0000000001-submissions-001.json"}]}}
            historical = page(None, "0000000001-25-000001", "10-K")
            responses = iter([primary, historical, {"cik": 1, "facts": {}}])
            real_client = SecClient("StockResearch test@example.com", root / "raw" / "sec",
                opener=lambda request, timeout: BytesIO(json.dumps(next(responses)).encode()),
                sleep=lambda delay: None,
                now=lambda: datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
            script = Path(__file__).resolve().parents[1] / "scripts" / "ingest_sec_filings.py"
            with patch.object(sys, "argv", [str(script), "--cik", "1", "--output-root", str(root)]), \
                 patch("stock_analyzer.sec_client.SecClient", return_value=real_client):
                runpy.run_path(str(script), run_name="__main__")
            ledger_path = next(root.glob("processed/*/*/filing_ledger.jsonl"))
            rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
            manifest = json.loads(ledger_path.with_name("manifest.json").read_text())
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(manifest["sources"]), 3)
            self.assertEqual(manifest["history_scope"], "all_declared_pages")
            current = next(row for row in rows if row["form"] == "10-Q")
            self.assertEqual(current["live_available_after_utc"], "2026-10-12T12:00:00Z")
            self.assertEqual(len(eligible_as_of(rows, "2026-10-12T12:00:00Z")), 1)
            self.assertTrue(all(Path(source["raw_path"]).exists() for source in manifest["sources"]))


if __name__ == "__main__":
    unittest.main()

