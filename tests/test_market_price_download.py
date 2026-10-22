import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import download_market_prices


class FakeSchedule:
    columns = ("open", "close")

    def __init__(self):
        self.loc = self

    def __getitem__(self, _slice):
        return self

    def iterrows(self):
        from datetime import datetime, timezone
        return iter([
            ("2026-01-02", {"close": datetime(2026, 1, 2, 21, tzinfo=timezone.utc)}),
            ("2026-01-05", {"close": datetime(2026, 1, 5, 21, tzinfo=timezone.utc)}),
        ])


class FakeHistory:
    columns = ("Close", "Adj Close")
    empty = False

    def iterrows(self):
        return iter([
            ("2026-01-02", {"Close": 100.0, "Adj Close": 100.0}),
            ("2026-01-05", {"Close": 101.0, "Adj Close": 100.5}),
        ])


class MarketPriceDownloadTests(unittest.TestCase):
    def test_writes_adjusted_prices_manifest_and_session_close_timestamps(self):
        history_calls = []

        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, **kwargs):
                history_calls.append((self.symbol, kwargs))
                return FakeHistory()

        fake_yf = types.ModuleType("yfinance")
        fake_yf.__version__ = "test-version"
        fake_yf.Ticker = FakeTicker
        fake_xcals = types.ModuleType("exchange_calendars")
        fake_xcals.__version__ = "test-version"
        fake_xcals.get_calendar = lambda name: types.SimpleNamespace(schedule=FakeSchedule())

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, {"yfinance": fake_yf, "exchange_calendars": fake_xcals}
        ):
            result = download_market_prices.download_prices(
                ["AAPL", "SPY"], "2026-01-01", "2026-01-06", Path(temp)
            )
            with result["csv_path"].open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            manifest = json.loads(result["manifest_path"].read_text(encoding="utf-8"))
            csv_sha256 = hashlib.sha256(result["csv_path"].read_bytes()).hexdigest()

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], {
            "symbol": "AAPL", "session_date": "2026-01-02",
            "close_at_utc": "2026-01-02T21:00:00Z", "adjusted_close": "100.0",
        })
        self.assertEqual(rows[1]["adjusted_close"], "100.5")
        self.assertEqual(manifest["row_count"], 4)
        self.assertEqual(manifest["calendar"], "XNYS via exchange_calendars")
        self.assertEqual(manifest["csv"]["sha256"], csv_sha256)
        self.assertTrue(all(call[1]["auto_adjust"] is False for call in history_calls))
        self.assertTrue(all("raise_errors" not in call[1] for call in history_calls))
        self.assertEqual([call[0] for call in history_calls], ["AAPL", "SPY"])

    def test_rejects_duplicate_symbols_before_network_access(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            download_market_prices.download_prices(
                ["AAPL", "aapl"], "2026-01-01", "2026-01-06", Path("unused")
            )


if __name__ == "__main__":
    unittest.main()
