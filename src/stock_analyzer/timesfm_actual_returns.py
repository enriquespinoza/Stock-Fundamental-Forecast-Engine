"""Build realized returns on the exact origins and horizons used by TimesFM."""

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

PRICE_COLUMNS = ("symbol", "session_date", "close_at_utc", "adjusted_close")
HORIZONS = (1, 5, 20)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError(f"Invalid timezone-aware timestamp: {value!r}") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"Timestamp must include a timezone: {value!r}")
    return result.astimezone(timezone.utc)


def _stamp(value: str | None) -> str:
    dt = _time(value) if value else datetime.now(timezone.utc)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _read_csv(path: Path) -> tuple[bytes, list[str], list[dict]]:
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    fields = reader.fieldnames or []
    if not fields or len(fields) != len(set(fields)):
        raise ValueError(f"CSV must have unique named columns: {path}")
    return raw, fields, list(reader)


def _load_prices(path: Path) -> tuple[bytes, dict[str, list[dict]]]:
    raw, fields, rows = _read_csv(path)
    if tuple(fields) != PRICE_COLUMNS:
        raise ValueError(f"Price CSV columns must be exactly {PRICE_COLUMNS}")
    by_symbol: dict[str, list[dict]] = {}
    seen = set()
    for row in rows:
        symbol = row["symbol"].strip().upper()
        session = row["session_date"].strip()
        try:
            datetime.strptime(session, "%Y-%m-%d")
            close_at = _time(row["close_at_utc"])
            close = float(row["adjusted_close"])
        except (ValueError, TypeError) as error:
            raise ValueError(f"Invalid price row for {symbol} {session}") from error
        if not symbol or not math.isfinite(close) or close <= 0:
            raise ValueError(f"Invalid symbol or adjusted close for {symbol} {session}")
        key = (symbol, session)
        if key in seen:
            raise ValueError(f"Duplicate price session: {symbol} {session}")
        seen.add(key)
        by_symbol.setdefault(symbol, []).append({"session_date": session,
                                                "close_at": close_at,
                                                "close": close})
    for series in by_symbol.values():
        series.sort(key=lambda item: item["session_date"])
    if not rows:
        raise ValueError("Price CSV contains no rows")
    return raw, by_symbol


def _forecast_number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(f"Invalid TimesFM forecast return: {value!r}") from error
    if not math.isfinite(number):
        raise ValueError(f"Invalid TimesFM forecast return: {value!r}")
    return number


def _metrics(rows: list[dict], horizon: int) -> dict:
    suffix = f"{horizon}s"
    matched = [row for row in rows if row["primary_sample_eligible"] == "true"
               and row["forecast_status"] == "OK" and row[f"status_{suffix}"] == "OK"]
    if not matched:
        return {"matched_count": 0}
    errors = [float(row[f"forecast_return_{suffix}"]) - float(row[f"actual_return_{suffix}"])
              for row in matched]
    actuals = [float(row[f"actual_return_{suffix}"]) for row in matched]
    forecast_sign_matches = [
        (float(row[f"forecast_return_{suffix}"]) > 0) == (float(row[f"actual_return_{suffix}"]) > 0)
        for row in matched if float(row[f"actual_return_{suffix}"]) != 0
        and float(row[f"forecast_return_{suffix}"]) != 0
    ]
    return {
        "matched_count": len(matched),
        "mae": sum(abs(error) for error in errors) / len(errors),
        "rmse": math.sqrt(sum(error * error for error in errors) / len(errors)),
        "mean_error_forecast_minus_actual": sum(errors) / len(errors),
        "directional_accuracy": (sum(forecast_sign_matches) / len(forecast_sign_matches)
                                 if forecast_sign_matches else None),
        "zero_return_baseline_mae": sum(abs(value) for value in actuals) / len(actuals),
        "zero_return_baseline_rmse": math.sqrt(sum(value * value for value in actuals) / len(actuals)),
    }


def _unique_origin_rows(rows: list[dict]) -> list[dict]:
    """Avoid counting multiple Form 4 rows with an identical forecast path twice."""
    unique: dict[tuple[str, str], dict] = {}
    comparable = ["forecast_status", *(f"forecast_return_{h}s" for h in HORIZONS),
                  *(f"actual_return_{h}s" for h in HORIZONS), *(f"status_{h}s" for h in HORIZONS)]
    for row in rows:
        origin = row.get("forecast_origin_session_date")
        key = (row["issuer_cik"], origin) if origin else (row["issuer_cik"], f"event:{row['event_id']}")
        prior = unique.get(key)
        if prior is None:
            unique[key] = row
        elif any(prior.get(field) != row.get(field) for field in comparable):
            raise ValueError(f"Events sharing forecast origin {key} have conflicting forecast/return values")
    return list(unique.values())


def build_timesfm_actual_returns(timesfm_path: Path, prices_path: Path,
                                 price_manifest_path: Path, output_root: Path, *,
                                 primary_start_year: int = 2010,
                                 built_at_utc: str | None = None) -> dict:
    """Attach realized 1/5/20-session returns using a verified outcome snapshot.

    The forecast's origin session and timestamp must match the outcome snapshot.
    Later adjusted-close revisions are recorded and returns use the origin and
    target closes from the same outcome snapshot.
    """
    timesfm_path, prices_path = Path(timesfm_path), Path(prices_path)
    price_manifest_path, output_root = Path(price_manifest_path), Path(output_root)
    timesfm_manifest_path = timesfm_path.parent / "manifest.json"
    timesfm_raw, timesfm_columns, features = _read_csv(timesfm_path)
    timesfm_manifest = json.loads(timesfm_manifest_path.read_text(encoding="utf-8"))
    price_manifest_raw = price_manifest_path.read_bytes()
    price_manifest = json.loads(price_manifest_raw.decode("utf-8"))
    if timesfm_manifest.get("schema_version") != "timesfm-event-features-manifest-v1":
        raise ValueError("Unsupported TimesFM feature manifest schema")
    feature_output = timesfm_manifest.get("output", {})
    if feature_output.get("sha256") != _sha(timesfm_raw) or feature_output.get("row_count") != len(features):
        raise ValueError("TimesFM feature CSV hash or row count does not match its manifest")
    price_input = timesfm_manifest.get("input_prices", {})
    prices_raw, prices = _load_prices(prices_path)
    if price_manifest.get("schema_version") != "market-price-snapshot-manifest-v1":
        raise ValueError("Unsupported market-price manifest schema")
    if price_manifest.get("csv", {}).get("sha256") != _sha(prices_raw):
        raise ValueError("Price CSV hash does not match its manifest")
    if price_manifest.get("row_count") is not None:
        _, _, price_rows = _read_csv(prices_path)
        if int(price_manifest["row_count"]) != len(price_rows):
            raise ValueError("Price CSV row count does not match its manifest")
    observed_at = _time(price_manifest.get("observed_at_utc", ""))
    feature_price_observed = (_time(price_input["observed_at_utc"])
                              if price_input.get("observed_at_utc") else None)
    if feature_price_observed and observed_at < feature_price_observed:
        raise ValueError("Outcome price snapshot predates the TimesFM feature price snapshot")

    required = {"event_id", "issuer_cik", "issuer_symbol", "event_available_at_utc",
                "forecast_status", "forecast_origin_session_date", "forecast_origin_close_at_utc",
                "forecast_origin_adjusted_close", *(f"forecast_return_{h}s" for h in HORIZONS)}
    missing = required - set(timesfm_columns)
    if missing:
        raise ValueError(f"TimesFM CSV missing required columns: {sorted(missing)}")
    ids = [row.get("event_id") for row in features]
    if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("TimesFM event IDs must be present and unique")

    output = []
    for feature in features:
        symbol = (feature.get("issuer_symbol") or "").strip().upper()
        event_at = _time(feature["event_available_at_utc"])
        generated_at = feature.get("forecast_generated_at_utc") or timesfm_manifest.get("built_at_utc")
        if generated_at:
            generated_at = _time(generated_at).isoformat().replace("+00:00", "Z")
        if feature["forecast_status"] == "OK" and not generated_at:
            raise ValueError("Successful TimesFM forecasts require a generation timestamp in the row or feature manifest")
        row = {
            "event_id": feature["event_id"], "issuer_cik": feature["issuer_cik"],
            "issuer_symbol": symbol, "event_available_at_utc": event_at.isoformat().replace("+00:00", "Z"),
            "primary_sample_eligible": str(event_at.year >= primary_start_year).lower(),
            "forecast_status": feature["forecast_status"],
            "forecast_generated_at_utc": generated_at,
            "forecast_origin_session_date": feature["forecast_origin_session_date"],
            "forecast_origin_close_at_utc": feature["forecast_origin_close_at_utc"],
            "forecast_origin_adjusted_close": feature["forecast_origin_adjusted_close"],
            "label_origin_adjusted_close": None,
            "origin_price_revision_ratio": None,
            "origin_adjusted_close_changed": None,
        }
        series = prices.get(symbol, [])
        origin_date = feature["forecast_origin_session_date"]
        origin_index = next((i for i, p in enumerate(series) if p["session_date"] == origin_date), None)
        if feature["forecast_status"] == "OK":
            if origin_index is None:
                raise ValueError(f"Forecast origin price missing for event {feature['event_id']}")
            origin = series[origin_index]
            if origin["close_at"] != _time(feature["forecast_origin_close_at_utc"]):
                raise ValueError(f"Forecast origin close timestamp mismatch for event {feature['event_id']}")
            if origin["close_at"] >= event_at:
                raise ValueError(f"Forecast origin is not strictly before event {feature['event_id']}")
            feature_origin = float(feature["forecast_origin_adjusted_close"])
            if not math.isfinite(feature_origin) or feature_origin <= 0:
                raise ValueError(f"Invalid forecast origin close value for event {feature['event_id']}")
            revision_ratio = origin["close"] / feature_origin
            row["label_origin_adjusted_close"] = format(origin["close"], ".12g")
            row["origin_price_revision_ratio"] = format(revision_ratio, ".12g")
            row["origin_adjusted_close_changed"] = str(not math.isclose(
                origin["close"], feature_origin, rel_tol=1e-10, abs_tol=1e-10)).lower()
        for horizon in HORIZONS:
            suffix = f"{horizon}s"
            forecast = _forecast_number(feature.get(f"forecast_return_{suffix}"))
            row[f"forecast_return_{suffix}"] = feature.get(f"forecast_return_{suffix}") or None
            row[f"actual_end_session_date_{suffix}"] = None
            row[f"actual_return_{suffix}"] = None
            row[f"benchmark_return_{suffix}"] = None
            row[f"excess_return_{suffix}"] = None
            row[f"forecast_error_{suffix}"] = None
            row[f"absolute_error_{suffix}"] = None
            row[f"direction_correct_{suffix}"] = None
            status = feature["forecast_status"] if feature["forecast_status"] != "OK" else "OK"
            end_index = origin_index + horizon if origin_index is not None else None
            if status == "OK" and end_index is not None:
                if end_index >= len(series):
                    status = "INSUFFICIENT_FUTURE_ISSUER_PRICES"
                else:
                    end = series[end_index]
                    if end["close_at"] > observed_at:
                        status = "PRICE_SNAPSHOT_PRECEDES_TARGET_CLOSE"
                    else:
                        actual = end["close"] / series[origin_index]["close"] - 1.0
                        benchmark = prices.get("SPY", [])
                        benchmark_by_date = {p["session_date"]: p for p in benchmark}
                        b_origin = benchmark_by_date.get(origin_date)
                        b_end = benchmark_by_date.get(end["session_date"])
                        if b_origin is not None and b_end is not None:
                            benchmark_return = b_end["close"] / b_origin["close"] - 1.0
                            row[f"benchmark_return_{suffix}"] = format(benchmark_return, ".12g")
                            row[f"excess_return_{suffix}"] = format(actual - benchmark_return, ".12g")
                        row[f"actual_end_session_date_{suffix}"] = end["session_date"]
                        row[f"actual_return_{suffix}"] = format(actual, ".12g")
                        if forecast is None:
                            status = "MISSING_TIMESFM_FORECAST_RETURN"
                        else:
                            error = forecast - actual
                            row[f"forecast_error_{suffix}"] = format(error, ".12g")
                            row[f"absolute_error_{suffix}"] = format(abs(error), ".12g")
                            if actual != 0:
                                row[f"direction_correct_{suffix}"] = str((forecast > 0) == (actual > 0)).lower()
            row[f"status_{suffix}"] = status
        output.append(row)

    stamp = _stamp(built_at_utc)
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output_path = folder / "event_timesfm_actual_returns.csv"
    columns = list(output[0]) if output else []
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)
    summary = {
        "schema_version": "timesfm-actual-return-evaluation-summary-v1",
        "event_count": len(output),
        "primary_sample_start_year": primary_start_year,
        "primary_sample_event_count": sum(row["primary_sample_eligible"] == "true" for row in output),
        "horizons_sessions": list(HORIZONS),
        "horizon_status_counts": {f"{h}s": {status: sum(row[f"status_{h}s"] == status for row in output)
                                            for status in sorted({row[f"status_{h}s"] for row in output})}
                                  for h in HORIZONS},
        "primary_sample_metrics_event_weighted": {f"{h}s": _metrics(output, h) for h in HORIZONS},
        "unique_forecast_origin_count": len(_unique_origin_rows(output)),
        "origin_adjusted_close_revision_count": sum(
            row["origin_adjusted_close_changed"] == "true" for row in output
        ),
        "primary_sample_metrics_unique_origins": {
            f"{h}s": _metrics(_unique_origin_rows(output), h) for h in HORIZONS
        },
        "forecast_status": "RESEARCH_ONLY_NO_ORDERS",
    }
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "timesfm-actual-return-evaluation-manifest-v1",
        "built_at_utc": stamp,
        "timesfm_features_input": {"path": str(timesfm_path), "sha256": _sha(timesfm_raw),
                                    "manifest_sha256": _sha(timesfm_manifest_path.read_bytes())},
        "feature_price_input": price_input,
        "price_input": {"path": str(prices_path), "sha256": _sha(prices_raw),
                        "manifest_path": str(price_manifest_path), "manifest_sha256": _sha(price_manifest_raw),
                        "observed_at_utc": price_manifest["observed_at_utc"]},
        "origin_revision_policy": "Validate forecast origin session and timestamp; compute returns from origin and target adjusted closes in the same outcome snapshot; record any origin price revision ratio.",
        "return_anchor": "TimesFM forecast origin adjusted close",
        "horizon_definition": "actual close at origin session index plus 1, 5, or 20 issuer trading sessions",
        "benchmark": "SPY when matching origin and end sessions are present",
        "metrics": "Primary sample only; forecast minus actual errors; zero-return baseline reported",
        "primary_sample_start_year": primary_start_year,
        "research_only": True,
        "forecast_status": "RESEARCH_ONLY_NO_ORDERS",
        "output": {"path": str(output_path), "sha256": _sha(output_path.read_bytes()), "row_count": len(output)},
        "summary_sha256": _sha(summary_path.read_bytes()),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "csv_path": output_path, "summary": summary, "manifest": manifest}
