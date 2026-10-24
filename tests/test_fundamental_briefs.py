import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from stock_analyzer.fundamental_briefs import build_fundamental_briefs


def fact(metric, value, year, *, instant=False, available_year=None):
    period_end = f"{year}-12-31"
    period_start = None if instant else f"{year}-01-01"
    filed_year = available_year or year + 1
    return {
        "issuer_cik": "0000000001", "metric": metric, "tag": metric,
        "tag_preference": 0, "unit": "USD", "value": str(value),
        "period_start": period_start, "period_end": period_end,
        "period_kind": "instant" if instant else "annual", "accession_number": f"0000000001-{filed_year}-{metric}-{year}",
        "form": "10-K", "accepted_at_utc": f"{filed_year}-02-01T00:00:00Z",
        "historical_available_after_utc": f"{filed_year}-02-01T00:01:00Z",
    }


class FundamentalBriefTests(unittest.TestCase):
    def test_builds_as_of_brief_with_provenance_ratios_and_review_prompts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            history = root / "financials" / "0000000001" / "2026-01-01T00-00-00Z"
            history.mkdir(parents=True)
            normalized_path = history / "normalized.json"
            facts = []
            for year, revenue, cfo in ((2022, 120, -10), (2023, 100, -20), (2024, 80, -30)):
                facts.extend([fact("revenue", revenue, year), fact("operating_cash_flow", cfo, year),
                              fact("operating_income", -5, year)])
            facts.extend([fact("assets", 100, 2024, instant=True),
                          fact("liabilities", 120, 2024, instant=True),
                          fact("equity", -20, 2024, instant=True),
                          fact("cash", 10, 2024, instant=True)])
            future = fact("revenue", 200, 2025, available_year=2026)
            future["historical_available_after_utc"] = "2026-02-01T00:01:00Z"
            future["accepted_at_utc"] = "2026-02-01T00:00:00Z"
            facts.append(future)
            quarter = fact("revenue", 90, 2025, available_year=2025)
            quarter.update({"period_start": "2025-07-01", "period_end": "2025-09-30",
                            "period_kind": "quarter", "form": "10-Q",
                            "accepted_at_utc": "2025-11-01T00:00:00Z",
                            "historical_available_after_utc": "2025-11-01T00:01:00Z",
                            "accession_number": "0000000001-25-10q-revenue"})
            facts.append(quarter)
            normalized = {"schema_version": "financial-normalization-v1", "issuer_cik": "0000000001",
                          "facts": facts, "issues": []}
            normalized_raw = (json.dumps(normalized) + "\n").encode()
            normalized_path.write_bytes(normalized_raw)
            (history / "manifest.json").write_text(json.dumps({
                "normalized_sha256": hashlib.sha256(normalized_raw).hexdigest()
            }), encoding="utf-8")
            universe = root / "universe.csv"
            universe.write_text("symbol,issuer_cik\nAAA,0000000001\n", encoding="utf-8")

            result = build_fundamental_briefs(root / "financials", universe,
                                              "2026-01-01T00:00:00Z", root / "out",
                                              built_at_utc="2026-01-01T00:00:00Z")
            brief = json.loads((result["output_dir"] / "issuer_fundamental_briefs.jsonl").read_text().splitlines()[0])
            self.assertEqual(brief["annual_10k"]["metrics"]["revenue"]["latest"]["value"], "80")
            self.assertEqual(brief["annual_10k"]["metrics"]["revenue"]["change_percent"], -20.0)
            self.assertEqual(brief["quarterly_10q"]["metrics"]["revenue"]["latest"]["value"], "90")
            self.assertEqual(brief["annual_10k"]["period_aligned_ratios"]["liabilities_to_assets"]["value"], 1.2)
            codes = {signal["code"] for signal in brief["review_signals"]}
            self.assertIn("TWO_LATEST_ANNUAL_CFO_PERIODS_NEGATIVE", codes)
            self.assertIn("REVENUE_DECLINED_TWICE", codes)
            self.assertIn("LIABILITIES_EXCEED_ASSETS", codes)
            self.assertNotIn(future["accession_number"], json.dumps(brief))
            self.assertEqual(result["summary"]["research_status"],
                             "DESCRIPTIVE_RESEARCH_ONLY_NO_ALLOCATION_EFFECT")


if __name__ == "__main__":
    unittest.main()
