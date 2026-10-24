from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from stock_analyzer.sec_client import SecClient, sec_archive_document_url


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class SecArchiveDocumentTests(unittest.TestCase):
    def test_url_builder_keeps_safe_nested_primary_document_path(self):
        url = sec_archive_document_url("0000320193", "0001193125-26-000001",
                                       "xslF345X06/report.html")
        self.assertEqual(url, "https://www.sec.gov/Archives/edgar/data/320193/"
                         "000119312526000001/xslF345X06/report.html")

    def test_url_builder_rejects_traversal_and_unexpected_suffixes(self):
        for document in ("../report.htm", "folder/../../report.html", "report.pdf", ""):
            with self.subTest(document=document), self.assertRaises(ValueError):
                sec_archive_document_url("320193", "0001193125-26-000001", document)

    def test_fetch_document_archives_raw_bytes_and_reuses_verified_cache(self):
        body = b"<html><title>FORM 8-K</title><body>Item 2.02</body></html>"
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, request.get_header("User-agent"), timeout))
            return _Response(body)

        with tempfile.TemporaryDirectory() as temp:
            client = SecClient("Research app contact@example.org", Path(temp), opener=opener,
                               now=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc))
            url = sec_archive_document_url("320193", "0001193125-26-000001", "report.htm")
            first = client.fetch_document(url)
            cached = client.fetch_document(url)
            self.assertEqual(first.payload, body)
            self.assertTrue(first.path.name.endswith(".document"))
            self.assertEqual(first.sha256, cached.sha256)
            self.assertFalse(first.from_cache)
            self.assertTrue(cached.from_cache)
            self.assertEqual(first.observed_at_utc, cached.observed_at_utc)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], "Research app contact@example.org")

    def test_fetch_document_rejects_non_sec_host_and_query_parameters(self):
        client = SecClient("Research app contact@example.org", Path("unused"), opener=lambda *a, **k: None)
        with self.assertRaisesRegex(ValueError, "SEC Archives"):
            client.fetch_document("https://example.org/Archives/edgar/data/320193/000119312526000001/a.htm")
        with self.assertRaisesRegex(ValueError, "SEC Archives"):
            client.fetch_document("https://www.sec.gov/Archives/edgar/data/320193/"
                                  "000119312526000001/a.htm?download=1")


if __name__ == "__main__":
    unittest.main()
