import hashlib
import unittest

from stock_analyzer.eight_k import parse_8k


def filing(**changes):
    row = {"issuer_cik": "0000320193", "accession_number": "0000000001-26-000001",
           "form": "8-K", "filing_date": "2026-10-22",
           "accepted_at_utc": "2026-10-22T12:00:00Z",
           "historical_available_after_utc": "2026-10-22T12:01:00Z",
           "live_available_after_utc": "2026-10-22T12:02:00Z",
           "availability_status": "TIMESTAMP_AVAILABLE",
           "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000000000126000001/a.htm"}
    row.update(changes)
    return row


class EightKIndexTests(unittest.TestCase):
    def test_extracts_unique_items_and_keeps_duplicate_mention_count(self):
        raw = b"""<html><head><title>FORM 8-K</title><style>Item 2.02</style></head>
        <body><p>Item 2.02 Results of Operations</p><p>Item 9.01 Financial Statements
        and Exhibits</p><p>ITEM 2.02. The company announced results.</p></body></html>"""
        result = parse_8k(raw, filing(), source_observed_at="2026-10-22T12:03:00Z")
        self.assertEqual([item["item"] for item in result["items"]], ["2.02", "9.01"])
        self.assertEqual(result["items"][0]["family"], "earnings_or_financial_update")
        self.assertEqual(result["items"][0]["mention_count"], 2)
        self.assertEqual(result["items"][1]["family"], "exhibits")
        self.assertEqual(result["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(result["historical_research_eligible"])

    def test_amendment_and_unknown_availability_are_preserved(self):
        raw = b"<html><title>FORM 8-K/A</title><body>Item 5.02.</body></html>"
        row = filing(form="8-K/A", accepted_at_utc=None,
                     historical_available_after_utc=None,
                     live_available_after_utc=None,
                     availability_status="UNKNOWN_ACCEPTANCE_TIME")
        result = parse_8k(raw, row, source_observed_at="2026-10-22T12:03:00Z")
        self.assertTrue(result["is_amendment"])
        self.assertFalse(result["historical_research_eligible"])
        self.assertEqual(result["items"][0]["item"], "5.02")

    def test_rejects_wrong_form_and_html_viewer_without_filing_marker(self):
        with self.assertRaisesRegex(ValueError, "8-K"):
            parse_8k(b"<html><title>FORM 10-Q</title></html>", filing(),
                     source_observed_at="2026-10-22T12:03:00Z")
        with self.assertRaisesRegex(ValueError, "8-K"):
            parse_8k(b"<html><title>SEC Filing Documents</title></html>", filing(),
                     source_observed_at="2026-10-22T12:03:00Z")
        with self.assertRaisesRegex(ValueError, "8-K or 8-K/A"):
            parse_8k(b"<html><title>FORM 8-K</title></html>", filing(form="10-K"),
                     source_observed_at="2026-10-22T12:03:00Z")
        with self.assertRaisesRegex(ValueError, "does not match ledger"):
            parse_8k(b"<html><title>FORM 8-K/A</title></html>", filing(),
                     source_observed_at="2026-10-22T12:03:00Z")

    def test_acquisition_cannot_precede_acceptance_and_availability_requires_timestamp(self):
        with self.assertRaisesRegex(ValueError, "cannot precede"):
            parse_8k(b"<html><title>FORM 8-K</title></html>", filing(),
                     source_observed_at="2026-10-22T11:59:59Z")
        with self.assertRaises(ValueError):
            parse_8k(b"<html><title>FORM 8-K</title></html>", filing(),
                     source_observed_at="2026-10-22T12:03:00")
        result = parse_8k(
            b"<html><title>FORM 8-K</title></html>",
            filing(availability_status="UNKNOWN_ACCEPTANCE_TIME"),
            source_observed_at="2026-10-22T12:03:00Z",
        )
        self.assertFalse(result["historical_research_eligible"])


if __name__ == "__main__":
    unittest.main()
