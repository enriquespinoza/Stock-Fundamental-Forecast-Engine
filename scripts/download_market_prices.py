"""Download AAPL and benchmark daily adjusted closes with XNYS session closes."""

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo


PRICE_COLUMNS = ("symbol", "session_date", "close_at_utc", "adjusted_close")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _calendar_date(value) -> str:
    return str(value)[:10]


def _close_column(columns) -> str:
    # exchange_calendars 4.x renamed these schedule fields in some releases.
    for candidate in ("close", "market_close"):
        if candidate in columns:
            return candidate
    raise ValueError("XNYS calendar schedule has no recognized close-time column")


def download_prices(symbols: list[str], start_date: str, end_date: str,
                    output_root: Path) -> dict:
    """Download immutable adjusted-close CSV and a provenance manifest.

    ``end_date`` follows yfinance's exclusive end-date convention. Close times
    come from the XNYS calendar rather than a fixed 4:00 p.m. assumption.
    """
    first = date.fromisoformat(start_date)
    end_exclusive = date.fromisoformat(end_date)
    if end_exclusive <= first:
        raise ValueError("End date must be later than start date")
    symbols = [symbol.strip().upper() for symbol in symbols]
    if not symbols or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) for s in symbols):
        raise ValueError("Provide valid uppercase ticker symbols")
    if len(symbols) != len(set(symbols)):
        raise ValueError("Ticker symbols must be unique")

    try:
        import exchange_calendars as xcals
        import yfinance as yf
    except ImportError as error:
        raise RuntimeError(
            "Install market-data extras with: python -m pip install -e .[market-data]"
        ) from error

    calendar = xcals.get_calendar("XNYS")
    schedule = calendar.schedule.loc[start_date:end_date]
    close_field = _close_column(schedule.columns)
    close_by_date = {}
    for session, row in schedule.iterrows():
        close_value = row[close_field]
        if hasattr(close_value, "to_pydatetime"):
            close_value = close_value.to_pydatetime()
        if close_value.tzinfo is None:
            close_value = close_value.replace(tzinfo=timezone.utc)
        close_by_date[_calendar_date(session)] = close_value.astimezone(timezone.utc)

    observed = datetime.now(timezone.utc)
    rows = []
    for symbol in symbols:
        history = yf.Ticker(symbol).history(
            start=start_date, end=end_date, interval="1d", auto_adjust=False,
            actions=False, repair=False,
        )
        if history.empty:
            raise ValueError(f"No daily price history returned for {symbol}")
        if "Adj Close" not in history.columns:
            raise ValueError(f"Yahoo response for {symbol} has no Adj Close column")
        for session, values in history.iterrows():
            session_date = _calendar_date(session)
            if session_date not in close_by_date:
                raise ValueError(f"Yahoo returned a non-XNYS session for {symbol}: {session_date}")
            close_at = close_by_date[session_date]
            # Exclude an in-progress daily bar if the script runs before the close.
            if close_at > observed:
                continue
            raw_price = values["Adj Close"]
            try:
                numeric_price = float(raw_price)
                price = Decimal(str(raw_price))
            except (TypeError, ValueError, InvalidOperation) as error:
                raise ValueError(f"Invalid adjusted close for {symbol} on {session_date}") from error
            if not math.isfinite(numeric_price) or not price.is_finite() or price <= 0:
                raise ValueError(f"Invalid adjusted close for {symbol} on {session_date}")
            rows.append((symbol, session_date, _utc_text(close_at), format(price, "f")))

    rows.sort(key=lambda row: (row[0], row[1]))
    if not rows:
        raise ValueError("No completed market sessions were downloaded")

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    observed_text = _utc_text(observed)
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    ticker_label = "_".join(symbols).lower()
    last_session = max(row[1] for row in rows)
    csv_path = output_root / f"{ticker_label}_daily_{first.isoformat()}_to_{last_session}_{stamp}.csv"
    manifest_path = csv_path.with_suffix(".manifest.json")
    if csv_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Snapshot output already exists: {csv_path}")

    import csv
    import io

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(PRICE_COLUMNS)
    writer.writerows(rows)
    csv_bytes = buffer.getvalue().encode("utf-8")
    csv_path.write_bytes(csv_bytes)
    manifest = {
        "schema_version": "market-price-snapshot-manifest-v1",
        "provider": "Yahoo Finance via yfinance",
        "provider_package_version": yf.__version__,
        "calendar": "XNYS via exchange_calendars",
        "calendar_package_version": xcals.__version__,
        "symbols": symbols,
        "start_date_inclusive": first.isoformat(),
        "end_date_exclusive": end_exclusive.isoformat(),
        "observed_at_utc": observed_text,
        "price_basis": "Yahoo Adj Close; auto_adjust=False; provider-adjusted historical close",
        "close_timestamp_basis": "XNYS calendar scheduled close, including early closes",
        "row_count": len(rows),
        "csv": {"path": str(csv_path), "sha256": _sha256(csv_bytes)},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"csv_path": csv_path, "manifest_path": manifest_path, "manifest": manifest}


def main() -> int:
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=("AAPL", "SPY"))
    parser.add_argument("--start-date", default="2010-01-01")
    parser.add_argument("--end-date", default=(today_ny + timedelta(days=1)).isoformat(),
                        help="Exclusive end date; defaults to tomorrow in New York time")
    parser.add_argument("--output-root", type=Path, default=Path("data/raw/market"))
    args = parser.parse_args()
    try:
        result = download_prices(args.symbols, args.start_date, args.end_date, args.output_root)
    except (ValueError, RuntimeError, FileExistsError) as error:
        print(f"Market price download failed: {error}", file=sys.stderr)
        return 1
    print(f"Price CSV: {result['csv_path']}")
    print(f"Manifest: {result['manifest_path']}")
    print(f"Observed at UTC: {result['manifest']['observed_at_utc']}")
    print(f"Rows: {result['manifest']['row_count']}")
    print(f"SHA-256: {result['manifest']['csv']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
