"""Build auditable, point-in-time forward return labels from daily market prices."""

import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from .ledger import parse_timestamp, utc_text


PRICE_COLUMNS = ("symbol", "session_date", "close_at_utc", "adjusted_close")
HORIZONS = (1, 5, 20)
PRIMARY_START_YEAR = 2010


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_prices(path: Path) -> tuple[list[dict], dict[str, list[dict]]]:
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    if tuple(reader.fieldnames or ()) != PRICE_COLUMNS:
        raise ValueError(f"Price CSV columns must be exactly: {', '.join(PRICE_COLUMNS)}")
    all_rows, by_symbol, seen = [], {}, set()
    for line_number, row in enumerate(reader, start=2):
        symbol = (row.get("symbol") or "").strip().upper()
        session_date = (row.get("session_date") or "").strip()
        try:
            datetime.strptime(session_date, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(f"Invalid market session date on price CSV line {line_number}") from error
        close_at = parse_timestamp(row.get("close_at_utc") or "")
        try:
            price = Decimal(row.get("adjusted_close") or "")
        except InvalidOperation as error:
            raise ValueError(f"Invalid adjusted close on price CSV line {line_number}") from error
        if not symbol or not price.is_finite() or price <= 0:
            raise ValueError(f"Invalid symbol or nonpositive adjusted close on line {line_number}")
        key = (symbol, session_date)
        if key in seen:
            raise ValueError(f"Duplicate symbol/session price row: {symbol} {session_date}")
        seen.add(key)
        item = {"symbol": symbol, "session_date": session_date, "close_at": close_at,
                "close_at_utc": utc_text(close_at), "adjusted_close": price}
        all_rows.append(item)
        by_symbol.setdefault(symbol, []).append(item)
    for rows in by_symbol.values():
        rows.sort(key=lambda item: item["session_date"])
    if not all_rows:
        raise ValueError("Price CSV contains no rows")
    return all_rows, by_symbol


def _number(value: Decimal | None) -> str | None:
    return format(value, ".12f") if value is not None else None


def _return(start: Decimal, end: Decimal) -> Decimal:
    return end / start - Decimal(1)


def _blank_label(event: dict, primary_start_year: int, status: str) -> dict:
    row = {
        "event_id": event["event_id"], "issuer_cik": event.get("issuer_cik"),
        "issuer_symbol": (event.get("issuer_symbol") or "").strip().upper() or None,
        "transaction_code": event.get("transaction_code"),
        "event_available_at_utc": utc_text(parse_timestamp(event["historical_available_after_utc"])),
        "benchmark_symbol": None,
        "primary_sample_eligible": str(
            parse_timestamp(event["historical_available_after_utc"]).year >= primary_start_year).lower(),
        "start_session_date": None, "start_close_at_utc": None, "start_adjusted_close": None,
        "label_status": status,
    }
    for horizon in HORIZONS:
        row.update({f"end_session_date_{horizon}d": None, f"forward_return_{horizon}d": None,
                    f"benchmark_return_{horizon}d": None, f"excess_return_{horizon}d": None,
                    f"label_status_{horizon}d": status})
    return row


def _label_event(event: dict, prices: dict[str, list[dict]], benchmark: str,
                 primary_start_year: int) -> dict:
    event_time = parse_timestamp(event["historical_available_after_utc"])
    symbol = (event.get("issuer_symbol") or "").strip().upper()
    benchmark = benchmark.strip().upper()
    if not event.get("event_id"):
        raise ValueError("Event ID is required")
    if not symbol:
        return _blank_label(event, primary_start_year, "MISSING_ISSUER_SYMBOL")
    issuer_rows = prices.get(symbol)
    benchmark_rows = prices.get(benchmark)
    if not issuer_rows:
        return _blank_label(event, primary_start_year, "NO_ISSUER_PRICE_HISTORY")
    if not benchmark_rows:
        return _blank_label(event, primary_start_year, "NO_BENCHMARK_PRICE_HISTORY")

    benchmark_by_date = {item["session_date"]: item for item in benchmark_rows}
    start_index = next((index for index, item in enumerate(issuer_rows)
                        if item["close_at"] > event_time), None)
    if start_index is None:
        return _blank_label(event, primary_start_year, "NO_ISSUER_CLOSE_AFTER_EVENT")

    start = issuer_rows[start_index]
    benchmark_start = benchmark_by_date.get(start["session_date"])
    if benchmark_start is not None and benchmark_start["close_at"] <= event_time:
        benchmark_start = None
    start_status = "LABELED" if benchmark_start is not None else "MISSING_BENCHMARK_START_SESSION"
    result = _blank_label(event, primary_start_year, start_status)
    result.update({"start_session_date": start["session_date"],
                   "benchmark_symbol": benchmark,
                   "start_close_at_utc": start["close_at_utc"],
                   "start_adjusted_close": _number(start["adjusted_close"]),
                   "label_status": start_status})
    for horizon in HORIZONS:
        end_index = start_index + horizon
        if end_index >= len(issuer_rows):
            result[f"label_status_{horizon}d"] = "INSUFFICIENT_FUTURE_ISSUER_PRICES"
            continue
        end = issuer_rows[end_index]
        issuer_return = _return(start["adjusted_close"], end["adjusted_close"])
        result.update({f"end_session_date_{horizon}d": end["session_date"],
                       f"forward_return_{horizon}d": _number(issuer_return)})
        if benchmark_start is None:
            result[f"label_status_{horizon}d"] = "MISSING_BENCHMARK_START_SESSION"
            continue
        benchmark_end = benchmark_by_date.get(end["session_date"])
        if benchmark_end is None:
            result[f"label_status_{horizon}d"] = "MISSING_BENCHMARK_END_SESSION"
            continue
        spy_return = _return(benchmark_start["adjusted_close"], benchmark_end["adjusted_close"])
        result.update({f"end_session_date_{horizon}d": end["session_date"],
                       f"forward_return_{horizon}d": _number(issuer_return),
                       f"benchmark_return_{horizon}d": _number(spy_return),
                       f"excess_return_{horizon}d": _number(issuer_return - spy_return),
                       f"label_status_{horizon}d": "OK"})
    return result


def build_forward_return_labels(events_path: Path, prices_path: Path, output_root: Path, *,
                                benchmark: str = "SPY", price_source: str,
                                price_basis: str, prices_observed_at: str,
                                primary_start_year: int = PRIMARY_START_YEAR,
                                built_at_utc: str | None = None) -> dict:
    """Label Form 4 candidate events with 1/5/20-session stock and SPY returns.

    A starting session is the first shared close whose actual UTC close timestamp
    is strictly after the public-availability timestamp. The price file must use
    consistent adjusted closes and provide actual session close timestamps.
    """
    events_path, prices_path, output_root = Path(events_path), Path(prices_path), Path(output_root)
    if not price_source.strip() or not price_basis.strip():
        raise ValueError("Price source and price basis must be documented")
    observed = utc_text(parse_timestamp(prices_observed_at))
    event_manifest_path = events_path.parent / "manifest.json"
    event_manifest = json.loads(event_manifest_path.read_text(encoding="utf-8"))
    events_raw, prices_raw = events_path.read_bytes(), prices_path.read_bytes()
    if _sha256(events_raw) != event_manifest.get("output", {}).get("sha256"):
        raise ValueError("Event CSV hash does not match its manifest")
    events = list(csv.DictReader(events_raw.decode("utf-8-sig").splitlines()))
    if len(events) != event_manifest.get("output", {}).get("row_count"):
        raise ValueError("Event CSV row count does not match its manifest")
    event_ids = [event.get("event_id") for event in events]
    if not all(event_ids) or len(event_ids) != len(set(event_ids)):
        raise ValueError("Event IDs must be present and unique")
    price_rows, prices = _read_prices(prices_path)
    benchmark = benchmark.strip().upper()
    if any((event.get("issuer_symbol") or "").strip().upper() == benchmark for event in events):
        raise ValueError("Benchmark cannot also be the issuer symbol for these events")
    labels = [_label_event(event, prices, benchmark, primary_start_year) for event in events]
    for row in labels:
        row["benchmark_symbol"] = benchmark

    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output_path = folder / "event_return_labels.csv"
    columns = list(labels[0]) if labels else [
        "event_id", "issuer_cik", "issuer_symbol", "transaction_code", "event_available_at_utc",
        "benchmark_symbol", "primary_sample_eligible", "start_session_date", "start_close_at_utc", "start_adjusted_close",
        "label_status", *[column for h in HORIZONS for column in (
            f"end_session_date_{h}d", f"forward_return_{h}d", f"benchmark_return_{h}d",
            f"excess_return_{h}d", f"label_status_{h}d")]]
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        writer.writerows(labels)
    summary = {
        "schema_version": "form4-forward-return-label-summary-v1",
        "event_count": len(events), "price_row_count": len(price_rows), "benchmark": benchmark,
        "primary_start_year": primary_start_year,
        "primary_sample_event_count": sum(row["primary_sample_eligible"] == "true" for row in labels),
        "events_with_start_close": sum(row["start_session_date"] is not None for row in labels),
        "horizon_status_counts": {f"{h}d": {
            status: sum(row[f"label_status_{h}d"] == status for row in labels)
            for status in sorted({row[f"label_status_{h}d"] for row in labels})}
            for h in HORIZONS},
        "label_status_counts": {status: sum(row["label_status"] == status for row in labels)
                                for status in sorted({row["label_status"] for row in labels})},
    }
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "form4-forward-return-label-manifest-v1", "built_at_utc": stamp,
        "event_input": {"path": str(events_path), "sha256": _sha256(events_raw)},
        "price_input": {"path": str(prices_path), "sha256": _sha256(prices_raw),
                        "observed_at_utc": observed, "source": price_source,
                        "basis": price_basis, "row_count": len(price_rows)},
        "output": {"path": str(output_path), "sha256": _sha256(output_path.read_bytes()),
                   "row_count": len(labels)},
        "summary_sha256": _sha256(summary_path.read_bytes()),
        "availability_policy": "first shared issuer/benchmark close strictly after historical filing availability",
        "horizons_sessions": list(HORIZONS), "benchmark": benchmark,
        "excess_return_definition": "issuer simple total return minus benchmark simple total return",
        "primary_sample_start_year": primary_start_year,
        "forecast_status": "LABELS_ONLY_NO_FORECAST",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    return {"output_dir": folder, "csv_path": output_path, "summary": summary, "manifest": manifest}
