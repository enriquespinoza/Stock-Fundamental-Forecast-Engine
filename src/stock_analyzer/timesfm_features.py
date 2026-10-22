"""Point-in-time TimesFM 2.5 price-path features for research only."""

import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re

PRICE_COLUMNS = ("symbol", "session_date", "close_at_utc", "adjusted_close")
HORIZONS = (1, 5, 20)
MODEL_ID = "google/timesfm-2.5-200m-pytorch"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid timezone-aware timestamp: {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_prices(path: Path) -> dict[str, list[dict]]:
    by_symbol: dict[str, list[dict]] = {}
    seen = set()
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != PRICE_COLUMNS:
            raise ValueError(f"Price CSV columns must be exactly {PRICE_COLUMNS}")
        for row in reader:
            symbol, session = row["symbol"], row["session_date"]
            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol or ""):
                raise ValueError(f"Invalid price symbol: {symbol!r}")
            try:
                datetime.strptime(session, "%Y-%m-%d")
                close = _timestamp(row["close_at_utc"])
                price = Decimal(row["adjusted_close"])
            except (TypeError, ValueError, InvalidOperation) as error:
                raise ValueError(f"Invalid price row for {symbol} on {session}") from error
            if not price.is_finite() or price <= 0:
                raise ValueError(f"Adjusted close must be positive and finite for {symbol} on {session}")
            key = (symbol, session)
            if key in seen:
                raise ValueError(f"Duplicate price session: {symbol} {session}")
            seen.add(key)
            by_symbol.setdefault(symbol, []).append({"session_date": session, "close_at": close, "price": float(price)})
    for rows in by_symbol.values():
        rows.sort(key=lambda item: (item["close_at"], item["session_date"]))
    return by_symbol


def _validate_manifests(events_path: Path, prices_path: Path, price_manifest_path: Path) -> tuple[dict, dict, bytes, bytes]:
    event_manifest_path = events_path.parent / "manifest.json"
    if not event_manifest_path.is_file():
        raise ValueError(f"Missing event manifest: {event_manifest_path}")
    event_raw, price_raw = events_path.read_bytes(), prices_path.read_bytes()
    event_manifest, price_manifest = _json(event_manifest_path), _json(price_manifest_path)
    event_record = event_manifest.get("output", {})
    if event_manifest.get("schema_version") != "form4-event-dataset-manifest-v1":
        raise ValueError("Unsupported event manifest schema")
    if event_record.get("sha256") != sha256(event_raw) or event_record.get("row_count") is None:
        raise ValueError("Event CSV hash does not match its manifest")
    price_record = price_manifest.get("csv", {})
    if price_manifest.get("schema_version") != "market-price-snapshot-manifest-v1":
        raise ValueError("Unsupported market-price manifest schema")
    if price_record.get("sha256") != sha256(price_raw):
        raise ValueError("Price CSV hash does not match its manifest")
    if price_manifest.get("row_count") is None:
        raise ValueError("Price manifest is missing row_count")
    if int(price_manifest["row_count"]) < 0:
        raise ValueError("Price manifest row_count must be nonnegative")
    _timestamp(price_manifest.get("observed_at_utc", ""))
    return event_manifest, price_manifest, event_raw, price_raw


class TimesFM25Forecaster:
    """Lazy adapter around the upstream TimesFM 2.5 PyTorch inference API."""

    def __init__(self, *, context_length: int = 512, checkpoint_revision: str | None = None):
        try:
            from timesfm import ForecastConfig, TimesFM_2p5_200M_torch
        except ImportError as error:
            raise RuntimeError(
                "TimesFM is optional. Install it with: python -m pip install -e .[timesfm]"
            ) from error
        kwargs = {"revision": checkpoint_revision} if checkpoint_revision else {}
        self.model = TimesFM_2p5_200M_torch.from_pretrained(MODEL_ID, **kwargs)
        self.model.compile(ForecastConfig(
            max_context=context_length,
            max_horizon=max(HORIZONS),
            normalize_inputs=True,
        ))
        self.checkpoint_revision = checkpoint_revision

    def forecast(self, values: list[float]) -> list[float]:
        point, _quantiles = self.model.forecast(horizon=max(HORIZONS), inputs=[values])
        return [float(value) for value in point[0][:max(HORIZONS)]]


def build_timesfm_features(events_path: Path, prices_path: Path, price_manifest_path: Path,
                            output_root: Path, *, forecaster=None, context_length: int = 512,
                            min_context: int = 32, model_id: str = MODEL_ID,
                            checkpoint_revision: str | None = None,
                            built_at_utc: str | None = None) -> dict:
    """Forecast adjusted-close paths from only sessions closed by each event timestamp.

    Forecast return fields are relative to the final close known at event time.
    They are model features, not the existing labeler's returns (whose start is
    the first close after event availability).
    """
    events_path, prices_path, price_manifest_path = map(Path, (events_path, prices_path, price_manifest_path))
    if not 1 <= context_length <= 16384 or not 2 <= min_context <= context_length:
        raise ValueError("Require 2 <= min_context <= context_length <= 16384")
    event_manifest, price_manifest, event_raw, price_raw = _validate_manifests(
        events_path, prices_path, price_manifest_path)
    prices = _read_prices(prices_path)
    price_observed = _timestamp(price_manifest["observed_at_utc"])
    actual_price_rows = sum(len(rows) for rows in prices.values())
    if actual_price_rows != int(price_manifest["row_count"]):
        raise ValueError("Price CSV row count does not match manifest")
    if forecaster is None:
        forecaster = TimesFM25Forecaster(context_length=context_length, checkpoint_revision=checkpoint_revision)
    if not hasattr(forecaster, "forecast"):
        raise TypeError("forecaster must provide forecast(values)")

    events = []
    with events_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"event_id", "issuer_symbol", "historical_available_after_utc"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"Event CSV must include {sorted(required)}")
        events = list(reader)
    if len(events) != event_manifest["output"]["row_count"]:
        raise ValueError("Event row count does not match manifest")
    ids = [row["event_id"] for row in events]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate event_id in input events")

    rows, cache = [], {}
    for event in events:
        event_id, symbol = event["event_id"], event["issuer_symbol"]
        if not symbol:
            raise ValueError(f"Event {event_id} has no issuer_symbol")
        available = _timestamp(event["historical_available_after_utc"])
        if price_observed < available:
            raise ValueError(f"Price snapshot observation precedes event availability for {event_id}")
        known = [row for row in prices.get(symbol, []) if row["close_at"] < available]
        result = {"event_id": event_id, "issuer_cik": event.get("issuer_cik"),
                  "issuer_symbol": symbol, "event_available_at_utc": available.isoformat().replace("+00:00", "Z"),
                  "forecast_status": None, "forecast_generated_at_utc": None, "forecast_origin_session_date": None,
                  "forecast_origin_close_at_utc": None, "forecast_origin_adjusted_close": None,
                  "context_rows": len(known), "context_start_session_date": None,
                  "context_end_session_date": None, "model_id": model_id}
        for horizon in HORIZONS:
            result[f"forecast_adjusted_close_{horizon}s"] = None
            result[f"forecast_return_{horizon}s"] = None
        if len(known) < min_context:
            result["forecast_status"] = "INSUFFICIENT_POINT_IN_TIME_CONTEXT"
            rows.append(result)
            continue
        context = known[-context_length:]
        origin = context[-1]
        values = [item["price"] for item in context]
        # Deduplicate identical symbol/context queries (common for filings sharing a close).
        context_key = (symbol, sha256(json.dumps(values, separators=(",", ":")).encode()))
        forecasts = cache.get(context_key)
        if forecasts is None:
            forecasts = list(forecaster.forecast(values))
            if len(forecasts) < max(HORIZONS) or any(not math.isfinite(float(value)) or float(value) <= 0 for value in forecasts[:max(HORIZONS)]):
                raise ValueError(f"Forecaster returned invalid or short path for {symbol}")
            cache[context_key] = forecasts
        generated = _timestamp(built_at_utc) if built_at_utc else datetime.now(timezone.utc)
        result["forecast_generated_at_utc"] = generated.isoformat(timespec="microseconds").replace("+00:00", "Z")
        result.update({"forecast_status": "OK", "forecast_origin_session_date": origin["session_date"],
                       "forecast_origin_close_at_utc": origin["close_at"].isoformat().replace("+00:00", "Z"),
                       "forecast_origin_adjusted_close": format(Decimal(str(origin["price"])), "f"),
                       "context_start_session_date": context[0]["session_date"],
                       "context_end_session_date": origin["session_date"]})
        for horizon in HORIZONS:
            forecast_price = float(forecasts[horizon - 1])
            result[f"forecast_adjusted_close_{horizon}s"] = forecast_price
            result[f"forecast_return_{horizon}s"] = forecast_price / origin["price"] - 1.0
        rows.append(result)

    built = _timestamp(built_at_utc) if built_at_utc else datetime.now(timezone.utc)
    stamp = built.isoformat(timespec="microseconds").replace("+00:00", "Z")
    folder = Path(output_root) / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output = folder / "event_timesfm_features.csv"
    columns = list(rows[0]) if rows else ["event_id", "issuer_cik", "issuer_symbol", "event_available_at_utc",
        "forecast_status", "forecast_origin_session_date", "forecast_origin_close_at_utc",
        "forecast_origin_adjusted_close", "context_rows", "context_start_session_date", "context_end_session_date",
        *[name for h in HORIZONS for name in (f"forecast_adjusted_close_{h}s", f"forecast_return_{h}s")], "model_id"]
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    summary = {"schema_version": "timesfm-event-features-summary-v1", "event_count": len(rows),
               "forecast_status_counts": {status: sum(row["forecast_status"] == status for row in rows)
                                          for status in sorted({row["forecast_status"] for row in rows})},
               "forecast_query_count": len(cache), "context_length_limit": context_length,
               "minimum_context_rows": min_context, "horizons_sessions": list(HORIZONS),
               "forecast_status": "RESEARCH_ONLY_NO_ORDERS"}
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"schema_version": "timesfm-event-features-manifest-v1", "built_at_utc": stamp,
        "input_events": {"path": str(events_path), "sha256": sha256(event_raw),
                         "manifest_path": str(events_path.parent / "manifest.json"),
                         "manifest_sha256": sha256((events_path.parent / "manifest.json").read_bytes())},
        "input_prices": {"path": str(prices_path), "sha256": sha256(price_raw),
                         "manifest_path": str(price_manifest_path),
                         "manifest_sha256": sha256(price_manifest_path.read_bytes()),
                         "observed_at_utc": price_manifest["observed_at_utc"]},
        "point_in_time_policy": "Use only price sessions with close_at_utc < event historical_available_after_utc",
        "forecast_timestamp_policy": "Record per-event forecast generation time immediately after inference; a retrospective replay timestamp does not establish prospective availability.",
        "price_adjustment_caveat": "Historical adjusted closes are sourced from a snapshot acquired at input_prices.observed_at_utc; provider back-adjustments may use later corporate-action data.",
        "model": {"id": model_id, "checkpoint_revision": checkpoint_revision,
                  "pretraining_cutoff_known": False,
                  "historical_forecast_claim_allowed": False},
        "features": {"horizons": list(HORIZONS), "returns_relative_to": "last adjusted close available by event timestamp",
                     "not_aligned_to_return_labels": "Existing labels start at the first close after event availability; reconcile forecast origin and target horizons before model training."},
        "research_only": True, "forecast_status": "RESEARCH_ONLY_NO_ORDERS",
        "output": {"path": str(output), "row_count": len(rows), "sha256": sha256(output.read_bytes())},
        "summary_sha256": sha256(summary_path.read_bytes())}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "csv_path": output, "summary": summary, "manifest": manifest}
