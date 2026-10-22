"""Fit, log, and score paired SEC-only versus SEC+TimesFM shadow forecasts."""

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re

HORIZONS = (1, 5, 20)
BASE_FEATURE_VERSION = "sec-event-financial-v1"
MODEL_ID = "ridge-median-impute-standardize-alpha-1.0-v1"
FEATURE_NAMES = (
    "transaction_direction", "ownership_direct", "ownership_indirect", "is_amendment",
    "10b5_1_value", "10b5_1_known", "log_abs_shares", "log_abs_reported_gross_value",
    "reporting_owner_count", "director_count", "officer_count", "ten_percent_owner_count",
    *(f"log_{metric}" for metric in ("revenue", "net_income", "operating_income",
      "operating_cash_flow", "capital_expenditures", "assets", "liabilities", "equity",
      "cash", "shares_outstanding", "free_cash_flow")),
    "net_income_to_revenue", "operating_income_to_revenue", "operating_cash_flow_to_revenue",
    "capital_expenditures_to_revenue", "free_cash_flow_to_revenue",
    "liabilities_to_assets", "equity_to_assets", "cash_to_assets",
)
TIMESFM_FEATURE_NAMES = tuple(f"timesfm_forecast_return_{h}s" for h in HORIZONS)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError(f"Invalid timezone-aware timestamp: {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _stamp(value: str | None = None) -> str:
    dt = _timestamp(value) if value else datetime.now(timezone.utc)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _read_csv(path: Path) -> tuple[bytes, list[str], list[dict]]:
    raw = Path(path).read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    columns = reader.fieldnames or []
    if not columns or len(columns) != len(set(columns)):
        raise ValueError(f"CSV must contain unique named columns: {path}")
    return raw, columns, list(reader)


def _manifested_csv(path: Path, expected_schema: str) -> tuple[bytes, dict, list[dict]]:
    path = Path(path)
    manifest_path = path.parent / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Missing input manifest: {manifest_path}")
    raw, _, rows = _read_csv(path)
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw.decode("utf-8"))
    if manifest.get("schema_version") != expected_schema:
        raise ValueError(f"Unsupported manifest schema in {manifest_path}")
    output = manifest.get("output", {})
    if output.get("sha256") != _sha(raw) or output.get("row_count") != len(rows):
        raise ValueError(f"CSV hash or row count does not match manifest: {path}")
    return manifest_raw, manifest, rows


def _number(value) -> float:
    if value in (None, ""):
        return math.nan
    try:
        result = float(value)
    except (ValueError, TypeError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _signed_log(value) -> float:
    number = _number(value)
    if not math.isfinite(number):
        return math.nan
    return math.copysign(math.log1p(abs(number)), number)


def _bool(value) -> float:
    if value is True or str(value).lower() == "true":
        return 1.0
    if value is False or str(value).lower() == "false":
        return 0.0
    return math.nan


def _ratio(numerator, denominator) -> float:
    n, d = _number(numerator), _number(denominator)
    if not math.isfinite(n) or not math.isfinite(d) or d == 0:
        return math.nan
    result = n / d
    return result if math.isfinite(result) else math.nan


def _features(row: dict, *, include_timesfm: bool) -> list[float]:
    """Explicit allow-list: never consumes return labels or future outcomes."""
    try:
        owners = json.loads(row.get("reporting_owners_json") or "[]")
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid reporting owner JSON for {row.get('event_id')}") from error
    if not isinstance(owners, list):
        raise ValueError(f"Reporting owners must be a list for {row.get('event_id')}")
    code = (row.get("transaction_code") or "").upper()
    shares = _number(row.get("shares"))
    gross = _number(row.get("reported_gross_value"))
    plan = row.get("aff10b5one")
    values = [
        1.0 if code == "P" else (-1.0 if code == "S" else 0.0),
        1.0 if row.get("ownership_type") == "D" else 0.0,
        1.0 if row.get("ownership_type") == "I" else 0.0,
        1.0 if row.get("is_amendment", "false").lower() == "true" else 0.0,
        _bool(plan), 1.0 if str(plan).lower() in ("true", "false") else 0.0,
        math.log1p(abs(shares)) if math.isfinite(shares) else math.nan,
        math.log1p(abs(gross)) if math.isfinite(gross) else math.nan,
        float(len(owners)),
        float(sum(bool(owner.get("is_director")) for owner in owners)),
        float(sum(bool(owner.get("is_officer")) for owner in owners)),
        float(sum(bool(owner.get("is_ten_percent_owner")) for owner in owners)),
    ]
    metrics = ("revenue", "net_income", "operating_income", "operating_cash_flow",
               "capital_expenditures", "assets", "liabilities", "equity", "cash",
               "shares_outstanding", "free_cash_flow")
    for metric in metrics:
        values.append(_signed_log(row.get(f"financial_{metric}_value")))
    values.extend([
        _ratio(row.get("financial_net_income_value"), row.get("financial_revenue_value")),
        _ratio(row.get("financial_operating_income_value"), row.get("financial_revenue_value")),
        _ratio(row.get("financial_operating_cash_flow_value"), row.get("financial_revenue_value")),
        _ratio(row.get("financial_capital_expenditures_value"), row.get("financial_revenue_value")),
        _ratio(row.get("financial_free_cash_flow_value"), row.get("financial_revenue_value")),
        _ratio(row.get("financial_liabilities_value"), row.get("financial_assets_value")),
        _ratio(row.get("financial_equity_value"), row.get("financial_assets_value")),
        _ratio(row.get("financial_cash_value"), row.get("financial_assets_value")),
    ])
    if include_timesfm:
        for horizon in HORIZONS:
            values.append(_number(row.get(f"timesfm_forecast_return_{horizon}s")))
    return values


def _validate_join(research_rows: list[dict], actual_rows: list[dict]) -> dict[str, dict]:
    research_ids = [row.get("event_id") for row in research_rows]
    actual_ids = [row.get("event_id") for row in actual_rows]
    if (not research_ids or any(not value for value in research_ids)
            or len(research_ids) != len(set(research_ids))):
        raise ValueError("Research event IDs must be present and unique")
    if len(actual_ids) != len(set(actual_ids)) or set(research_ids) != set(actual_ids):
        raise ValueError("Research and aligned-return event IDs must match exactly")
    actual = {row["event_id"]: row for row in actual_rows}
    for event in research_rows:
        outcome = actual[event["event_id"]]
        for left, right in (("issuer_cik", "issuer_cik"),
                            ("issuer_symbol", "issuer_symbol"),
                            ("timesfm_forecast_origin_session_date", "forecast_origin_session_date")):
            if event.get(left) != outcome.get(right):
                raise ValueError(f"Issuer/origin mismatch for event {event['event_id']}")
        event_time = _timestamp(event["historical_available_after_utc"])
        outcome_time = _timestamp(outcome["event_available_at_utc"])
        if event_time != outcome_time:
            raise ValueError(f"Event availability mismatch for event {event['event_id']}")
    return actual


def _training_weights(rows: list[dict]) -> list[float]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["issuer_cik"], row["historical_available_after_utc"])
        counts[key] = counts.get(key, 0) + 1
    raw = [1.0 / counts[(row["issuer_cik"], row["historical_available_after_utc"])] for row in rows]
    scale = len(raw) / sum(raw)
    return [weight * scale for weight in raw]


def train_shadow_model_pair(research_path: Path, actual_returns_path: Path,
                            output_root: Path, *, training_cutoff_utc: str,
                            timesfm_feature_start_utc: str,
                            code_revision: str,
                            frozen_at_utc: str | None = None,
                            minimum_training_rows: int = 40,
                            primary_start_year: int = 2010,
                            built_at_utc: str | None = None) -> dict:
    """Fit paired Ridge models on matured outcomes strictly before the cutoff."""
    try:
        import joblib
        import numpy as np
        import sklearn
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as error:
        raise RuntimeError("Install the optional research extra to train shadow models") from error
    research_path, actual_returns_path, output_root = map(Path, (research_path, actual_returns_path, output_root))
    research_manifest_raw, research_manifest, research_rows = _manifested_csv(
        research_path, "form4-event-research-timesfm-manifest-v1")
    actual_manifest_raw, actual_manifest, actual_rows = _manifested_csv(
        actual_returns_path, "timesfm-actual-return-evaluation-manifest-v1")
    actual_by_id = _validate_join(research_rows, actual_rows)
    cutoff = _timestamp(training_cutoff_utc)
    feature_start = _timestamp(timesfm_feature_start_utc)
    if not re.fullmatch(r"[0-9a-f]{40,64}", code_revision):
        raise ValueError("Provide the full hexadecimal source-control commit ID used for training")
    if feature_start >= cutoff:
        raise ValueError("TimesFM prospective feature-capture start must precede the training cutoff")
    prices_observed = _timestamp(actual_manifest.get("price_input", {}).get("observed_at_utc", ""))
    if prices_observed > cutoff:
        raise ValueError("Training cutoff precedes observation of the outcome price snapshot")
    feature_input = actual_manifest.get("timesfm_features_input", {})
    feature_path = Path(feature_input.get("path", ""))
    feature_manifest_path = feature_path.parent / "manifest.json"
    feature_manifest_raw = feature_manifest_path.read_bytes()
    if _sha(feature_manifest_raw) != feature_input.get("manifest_sha256"):
        raise ValueError("TimesFM feature manifest hash differs from actual-return provenance")
    feature_manifest = json.loads(feature_manifest_raw.decode("utf-8"))
    checkpoint = feature_manifest.get("model", {})
    checkpoint_revision = checkpoint.get("checkpoint_revision")
    if not checkpoint_revision or not re.fullmatch(r"[0-9a-f]{40}", checkpoint_revision):
        raise ValueError("Pin TimesFM to a full immutable 40-character checkpoint revision before model training")
    frozen = _timestamp(frozen_at_utc) if frozen_at_utc else datetime.now(timezone.utc)
    if frozen < cutoff:
        raise ValueError("Model freeze timestamp cannot precede the training cutoff")
    eligible = []
    for event in research_rows:
        outcome = actual_by_id[event["event_id"]]
        event_time = _timestamp(event["historical_available_after_utc"])
        if event_time < feature_start or event_time >= cutoff or event_time.year < primary_start_year:
            continue
        if event.get("timesfm_forecast_status") != "OK" or outcome.get("forecast_status") != "OK":
            continue
        eligible.append((event, outcome))

    fitted = {"baseline": {}, "augmented": {}}
    horizon_summary = {}
    for horizon in HORIZONS:
        suffix = f"{horizon}s"
        rows = [(event, outcome) for event, outcome in eligible
                if outcome.get(f"status_{suffix}") == "OK"
                and outcome.get(f"actual_return_{suffix}") not in (None, "")
                and outcome.get(f"actual_end_session_date_{suffix}", "") < cutoff.date().isoformat()]
        if len(rows) < minimum_training_rows:
            raise ValueError(f"Only {len(rows)} matured training rows for {suffix}; need {minimum_training_rows}")
        targets = np.asarray([float(outcome[f"actual_return_{suffix}"]) for _, outcome in rows])
        if not np.isfinite(targets).all():
            raise ValueError(f"Nonfinite target in {suffix} training rows")
        sample_weight = _training_weights([event for event, _ in rows])
        for track, include_timesfm in (("baseline", False), ("augmented", True)):
            matrix = np.asarray([_features(event, include_timesfm=include_timesfm) for event, _ in rows], dtype=float)
            model = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                                  StandardScaler(), Ridge(alpha=1.0))
            model.fit(matrix, targets, ridge__sample_weight=np.asarray(sample_weight))
            fitted[track][suffix] = model
        horizon_summary[suffix] = {
            "training_row_count": len(rows),
            "unique_issuer_availability_groups": len({
                (event["issuer_cik"], event["historical_available_after_utc"]) for event, _ in rows
            }),
            "target_end_date_max": max(outcome[f"actual_end_session_date_{suffix}"] for _, outcome in rows),
        }

    stamp = _stamp(built_at_utc)
    freeze_stamp = frozen.isoformat(timespec="microseconds").replace("+00:00", "Z")
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    model_path = folder / "paired_ridge_models.joblib"
    bundle = {"schema_version": "shadow-model-pair-v1", "model_id": MODEL_ID,
              "feature_version": BASE_FEATURE_VERSION, "horizons": list(HORIZONS),
              "frozen_at_utc": freeze_stamp, "training_cutoff_utc": _stamp(training_cutoff_utc),
              "models": fitted}
    joblib.dump(bundle, model_path)
    summary = {"schema_version": "shadow-model-training-summary-v1",
               "model_id": MODEL_ID, "training_cutoff_utc": _stamp(training_cutoff_utc),
               "frozen_at_utc": freeze_stamp, "horizon_training": horizon_summary,
               "forecast_status": "FROZEN_SHADOW_MODELS_NOT_CONNECTED_TO_ORDERS"}
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "shadow-model-pair-manifest-v1", "built_at_utc": stamp,
        "model_id": MODEL_ID, "feature_version": BASE_FEATURE_VERSION,
        "estimator": {"class": "sklearn.linear_model.Ridge", "alpha": 1.0,
                      "preprocessing": ["median imputation", "standard scaling"],
                      "sample_weight": "inverse rows per issuer and event availability timestamp"},
        "tracks": {"baseline": "SEC transaction/owner/financial features",
                   "augmented": "same SEC features plus TimesFM 1/5/20-session forecast returns"},
        "training_cutoff_utc": _stamp(training_cutoff_utc), "frozen_at_utc": freeze_stamp,
        "primary_start_year": primary_start_year,
        "cohort_issuer_ciks": sorted({event["issuer_cik"] for event, _ in eligible}),
        "cohort_issuer_ciks_sha256": _sha(json.dumps(
            sorted({event["issuer_cik"] for event, _ in eligible}), separators=(",", ":")).encode()),
        "research_dataset_input": {"path": str(research_path), "sha256": _sha(research_path.read_bytes()),
                                   "manifest_sha256": _sha(research_manifest_raw)},
        "actual_returns_input": {"path": str(actual_returns_path), "sha256": _sha(actual_returns_path.read_bytes()),
                                 "manifest_sha256": _sha(actual_manifest_raw)},
        "timesfm_features_manifest_sha256": actual_manifest.get("timesfm_features_input", {}).get("manifest_sha256"),
        "timesfm_checkpoint": {"model_id": checkpoint.get("id"),
                               "checkpoint_revision": checkpoint_revision,
                               "prospective_feature_capture_start_utc": _stamp(timesfm_feature_start_utc)},
        "feature_schema": {"baseline": list(FEATURE_NAMES),
                           "augmented": [*FEATURE_NAMES, *TIMESFM_FEATURE_NAMES],
                           "sha256": _sha(json.dumps(
                               {"baseline": FEATURE_NAMES, "augmented": (*FEATURE_NAMES, *TIMESFM_FEATURE_NAMES)},
                               separators=(",", ":")).encode())},
        "code_revision": code_revision,
        "software": {"scikit_learn": sklearn.__version__},
        "model_artifact": {"path": str(model_path), "sha256": _sha(model_path.read_bytes())},
        "summary_sha256": _sha(summary_path.read_bytes()),
        "point_in_time_policy": "training event and target outcomes must be available before the explicit training cutoff",
        "research_only": True, "forecast_status": "FROZEN_SHADOW_MODELS_NOT_CONNECTED_TO_ORDERS",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "model_path": model_path, "summary": summary, "manifest": manifest}


def build_shadow_predictions(research_path: Path, model_dir: Path, output_root: Path, *,
                             built_at_utc: str | None = None) -> dict:
    """Write paired predictions only for events arriving after frozen model time."""
    try:
        import joblib
        import numpy as np
    except ImportError as error:
        raise RuntimeError("Install the optional research extra to predict shadow models") from error
    research_path, model_dir, output_root = map(Path, (research_path, model_dir, output_root))
    input_manifest_raw, input_manifest, research_rows = _manifested_csv(
        research_path, "form4-event-research-timesfm-manifest-v1")
    stamp = _stamp(built_at_utc)
    created_at = _timestamp(stamp)
    model_manifest_path = model_dir / "manifest.json"
    model_manifest_raw = model_manifest_path.read_bytes()
    model_manifest = json.loads(model_manifest_raw.decode("utf-8"))
    model_path = model_dir / "paired_ridge_models.joblib"
    if model_manifest.get("schema_version") != "shadow-model-pair-manifest-v1":
        raise ValueError("Unsupported frozen shadow model manifest")
    if model_manifest.get("model_artifact", {}).get("sha256") != _sha(model_path.read_bytes()):
        raise ValueError("Frozen model artifact hash does not match manifest")
    bundle = joblib.load(model_path)
    if bundle.get("schema_version") != "shadow-model-pair-v1" or bundle.get("model_id") != model_manifest.get("model_id"):
        raise ValueError("Frozen model artifact metadata does not match its manifest")
    frozen = _timestamp(bundle["frozen_at_utc"])
    cohort = set(model_manifest.get("cohort_issuer_ciks", []))
    if not cohort:
        raise ValueError("Frozen model manifest has no fixed issuer cohort")
    feature_input = input_manifest.get("timesfm_features_input", {})
    feature_path = Path(feature_input.get("path", ""))
    feature_manifest_path = feature_path.parent / "manifest.json"
    feature_manifest_raw = feature_manifest_path.read_bytes()
    if _sha(feature_manifest_raw) != feature_input.get("manifest_sha256"):
        raise ValueError("TimesFM feature manifest hash differs from research dataset provenance")
    feature_manifest = json.loads(feature_manifest_raw.decode("utf-8"))
    checkpoint = feature_manifest.get("model", {})
    revision = checkpoint.get("checkpoint_revision")
    if not revision or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Prospective predictions require a pinned full TimesFM checkpoint revision")
    price_input = feature_manifest.get("input_prices", {})
    prices_path = Path(price_input.get("path", ""))
    prices_raw, price_columns, price_rows = _read_csv(prices_path)
    price_manifest_path = Path(price_input.get("manifest_path", ""))
    price_manifest_raw = price_manifest_path.read_bytes()
    if tuple(price_columns) != ("symbol", "session_date", "close_at_utc", "adjusted_close"):
        raise ValueError("TimesFM source price CSV has unexpected columns")
    if _sha(prices_raw) != price_input.get("sha256") or _sha(price_manifest_raw) != price_input.get("manifest_sha256"):
        raise ValueError("TimesFM source prices do not match feature provenance")
    price_manifest = json.loads(price_manifest_raw.decode("utf-8"))
    if price_manifest.get("csv", {}).get("sha256") != _sha(prices_raw):
        raise ValueError("TimesFM source price hash does not match price manifest")
    prices_by_symbol: dict[str, list[dict]] = {}
    for price in price_rows:
        prices_by_symbol.setdefault(price["symbol"].upper(), []).append(price)
    for series in prices_by_symbol.values():
        series.sort(key=lambda price: price["session_date"])
    event_ids = [row.get("event_id") for row in research_rows]
    if len(event_ids) != len(set(event_ids)) or any(not event_id for event_id in event_ids):
        raise ValueError("Prediction event IDs must be present and unique")
    future = []
    excluded_matured_events = 0
    for row in research_rows:
        if row.get("issuer_cik") not in cohort:
            raise ValueError(f"Event issuer {row.get('issuer_cik')} is outside the frozen model cohort")
        event_at = _timestamp(row["historical_available_after_utc"])
        if event_at <= frozen:
            continue
        if row.get("timesfm_forecast_status") != "OK":
            continue
        if row.get("timesfm_model_id") != checkpoint.get("id"):
            raise ValueError(f"TimesFM model ID mismatch for event {row.get('event_id')}")
        origin_at = _timestamp(row["timesfm_forecast_origin_close_at_utc"])
        if origin_at >= event_at:
            raise ValueError(f"TimesFM origin must precede event {row['event_id']}")
        series = prices_by_symbol.get((row.get("issuer_symbol") or "").upper(), [])
        origin_index = next((i for i, price in enumerate(series)
                             if price["session_date"] == row["timesfm_forecast_origin_session_date"]), None)
        if origin_index is None or origin_index + 20 >= len(series):
            # Not enough future observations to have matured at this snapshot.
            pass
        else:
            end = series[origin_index + 20]
            end_close_at = _timestamp(end["close_at_utc"])
            if end_close_at <= created_at:
                excluded_matured_events += 1
                continue
        future.append(row)
    if not future:
        raise ValueError("No eligible post-freeze events to score")
    predictions = []
    for event in future:
        row = {"event_id": event["event_id"], "issuer_cik": event["issuer_cik"],
               "issuer_symbol": event["issuer_symbol"],
               "transaction_code": event.get("transaction_code"),
               "event_available_at_utc": _stamp(event["historical_available_after_utc"]),
               "forecast_created_at_utc": stamp,
               "forecast_origin_session_date": event["timesfm_forecast_origin_session_date"],
               "forecast_origin_close_at_utc": event["timesfm_forecast_origin_close_at_utc"],
               "forecast_origin_adjusted_close": event["timesfm_forecast_origin_adjusted_close"],
               "timesfm_forecast_status": event["timesfm_forecast_status"]}
        for horizon in HORIZONS:
            suffix = f"{horizon}s"
            base = np.asarray([_features(event, include_timesfm=False)], dtype=float)
            enhanced = np.asarray([_features(event, include_timesfm=True)], dtype=float)
            row[f"sec_baseline_pred_return_{suffix}"] = format(
                float(bundle["models"]["baseline"][suffix].predict(base)[0]), ".12g")
            row[f"sec_timesfm_pred_return_{suffix}"] = format(
                float(bundle["models"]["augmented"][suffix].predict(enhanced)[0]), ".12g")
        predictions.append(row)
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output_path = folder / "paired_shadow_predictions.csv"
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(predictions[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(predictions)
    summary = {"schema_version": "paired-shadow-predictions-summary-v1",
               "prediction_count": len(predictions),
               "issuer_count": len({row["issuer_cik"] for row in predictions}),
               "forecast_status": "PROSPECTIVE_SHADOW_ONLY_NO_ORDERS"}
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "paired-shadow-predictions-manifest-v1", "built_at_utc": stamp,
        "model_input": {"path": str(model_path), "sha256": _sha(model_path.read_bytes()),
                        "manifest_sha256": _sha(model_manifest_raw),
                        "model_id": model_manifest["model_id"], "frozen_at_utc": model_manifest["frozen_at_utc"]},
        "research_dataset_input": {"path": str(research_path), "sha256": _sha(research_path.read_bytes()),
                                   "manifest_sha256": _sha(input_manifest_raw)},
        "event_filter": "event historical availability strictly after frozen model timestamp",
        "matured_event_filter": "exclude any event whose 20-session target close is already known at forecast creation",
        "excluded_already_matured_event_count": excluded_matured_events,
        "timesfm_checkpoint": {"model_id": checkpoint.get("id"), "revision": revision},
        "feature_policy": "explicit SEC/financial allow-list plus TimesFM return forecasts; label columns are never read",
        "output": {"path": str(output_path), "row_count": len(predictions), "sha256": _sha(output_path.read_bytes())},
        "summary_sha256": _sha(summary_path.read_bytes()),
        "research_only": True, "forecast_status": "PROSPECTIVE_SHADOW_ONLY_NO_ORDERS",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "csv_path": output_path, "summary": summary, "manifest": manifest}


def _loss_metrics(rows: list[dict], horizon: int) -> dict:
    suffix = f"{horizon}s"
    matched = [row for row in rows if row.get(f"status_{suffix}") == "OK"
               and row.get("forecast_status") == "OK"]
    if not matched:
        return {"matched_count": 0}
    actual = [_number(row.get(f"actual_return_{suffix}")) for row in matched]
    base = [_number(row.get(f"sec_baseline_pred_return_{suffix}")) for row in matched]
    augmented = [_number(row.get(f"sec_timesfm_pred_return_{suffix}")) for row in matched]
    if not all(math.isfinite(value) for values in (actual, base, augmented) for value in values):
        raise ValueError(f"Nonfinite prediction or realized return for {suffix}")
    baseline_errors = [prediction - value for prediction, value in zip(base, actual)]
    augmented_errors = [prediction - value for prediction, value in zip(augmented, actual)]
    baseline_abs = [abs(error) for error in baseline_errors]
    augmented_abs = [abs(error) for error in augmented_errors]
    baseline_sq = [error * error for error in baseline_errors]
    augmented_sq = [error * error for error in augmented_errors]
    direction = lambda prediction, value: (prediction > 0) == (value > 0)
    return {
        "matched_count": len(matched),
        "sec_baseline_mae": sum(baseline_abs) / len(matched),
        "sec_timesfm_mae": sum(augmented_abs) / len(matched),
        "mae_delta_augmented_minus_baseline": (sum(augmented_abs) - sum(baseline_abs)) / len(matched),
        "sec_baseline_rmse": math.sqrt(sum(baseline_sq) / len(matched)),
        "sec_timesfm_rmse": math.sqrt(sum(augmented_sq) / len(matched)),
        "rmse_delta_augmented_minus_baseline": math.sqrt(sum(augmented_sq) / len(matched)) - math.sqrt(sum(baseline_sq) / len(matched)),
        "sec_baseline_mean_error": sum(baseline_errors) / len(matched),
        "sec_timesfm_mean_error": sum(augmented_errors) / len(matched),
        "sec_baseline_directional_accuracy": sum(direction(p, a) for p, a in zip(base, actual)) / len(matched),
        "sec_timesfm_directional_accuracy": sum(direction(p, a) for p, a in zip(augmented, actual)) / len(matched),
        "zero_return_mae": sum(abs(value) for value in actual) / len(matched),
        "zero_return_rmse": math.sqrt(sum(value * value for value in actual) / len(matched)),
    }


def _grouped_loss_metrics(rows: list[dict], horizon: int, group_field: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = row.get(group_field) or "unknown"
        groups.setdefault(key, []).append(row)
    return {key: _loss_metrics(group, horizon) for key, group in sorted(groups.items())}


def _cluster_ci(rows: list[dict], horizon: int, *, replicates: int, seed: int) -> dict:
    """Percentile CI for paired absolute-loss deltas resampling issuer-weeks."""
    suffix = f"{horizon}s"
    clusters: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        if row.get(f"status_{suffix}") != "OK" or row.get("forecast_status") != "OK":
            continue
        actual = _number(row.get(f"actual_return_{suffix}"))
        base = _number(row.get(f"sec_baseline_pred_return_{suffix}"))
        augmented = _number(row.get(f"sec_timesfm_pred_return_{suffix}"))
        date = datetime.strptime(row["event_available_at_utc"][:10], "%Y-%m-%d").date()
        week = date.isocalendar()
        key = (row["issuer_cik"], f"{week.year}-W{week.week:02d}")
        clusters.setdefault(key, []).append(abs(augmented - actual) - abs(base - actual))
    if not clusters:
        return {"cluster_count": 0, "confidence_level": 0.95, "ci_low": None, "ci_high": None}
    values = list(clusters.values())
    if len(values) < 2:
        return {"cluster_count": len(values), "confidence_level": 0.95, "ci_low": None, "ci_high": None}
    rng = random.Random(seed)
    draws = []
    for _ in range(replicates):
        chosen = [rng.choice(values) for _ in values]
        flat = [delta for group in chosen for delta in group]
        draws.append(sum(flat) / len(flat))
    draws.sort()
    return {"cluster_count": len(values), "confidence_level": 0.95,
            "ci_low": draws[int(0.025 * (replicates - 1))],
            "ci_high": draws[int(0.975 * (replicates - 1))],
            "method": "issuer-week cluster bootstrap; resampled clusters preserve event multiplicity"}


def score_shadow_predictions(predictions_path: Path, actual_returns_path: Path,
                             prices_path: Path, price_manifest_path: Path,
                             output_root: Path, *, replicates: int = 2000,
                             seed: int = 20261022, scored_at_utc: str | None = None) -> dict:
    """Score logged prospective forecasts only after their matching closes mature."""
    if replicates < 100:
        raise ValueError("Use at least 100 bootstrap replicates")
    predictions_path, actual_returns_path, prices_path, price_manifest_path, output_root = map(
        Path, (predictions_path, actual_returns_path, prices_path, price_manifest_path, output_root))
    _, prediction_columns, predictions = _read_csv(predictions_path)
    prediction_manifest_path = predictions_path.parent / "manifest.json"
    prediction_manifest_raw = prediction_manifest_path.read_bytes()
    prediction_manifest = json.loads(prediction_manifest_raw.decode("utf-8"))
    if prediction_manifest.get("schema_version") != "paired-shadow-predictions-manifest-v1":
        raise ValueError("Unsupported paired prediction manifest schema")
    if prediction_manifest.get("output", {}).get("sha256") != _sha(predictions_path.read_bytes()):
        raise ValueError("Prediction CSV hash does not match its immutable manifest")
    _, actual_manifest, actuals = _manifested_csv(
        actual_returns_path, "timesfm-actual-return-evaluation-manifest-v1")
    actual_manifest_raw = (actual_returns_path.parent / "manifest.json").read_bytes()
    price_manifest_raw = price_manifest_path.read_bytes()
    price_manifest = json.loads(price_manifest_raw.decode("utf-8"))
    price_raw, price_columns, price_rows = _read_csv(prices_path)
    price_input = actual_manifest.get("price_input", {})
    if price_input.get("sha256") != _sha(price_raw):
        raise ValueError("Price CSV differs from the snapshot used to build actual returns")
    if price_input.get("manifest_sha256") != _sha(price_manifest_raw):
        raise ValueError("Price manifest differs from the snapshot used to build actual returns")
    if price_manifest.get("csv", {}).get("sha256") != _sha(price_raw):
        raise ValueError("Price CSV hash does not match its manifest")
    if prediction_manifest.get("research_only") is not True:
        raise ValueError("Prediction manifest is not marked research-only")
    if not predictions:
        raise ValueError("Prediction CSV has no rows")
    required = {"event_id", "issuer_cik", "issuer_symbol", "event_available_at_utc",
                "forecast_created_at_utc", "forecast_origin_session_date", "forecast_origin_close_at_utc",
                "forecast_origin_adjusted_close",
                *(f"sec_baseline_pred_return_{h}s" for h in HORIZONS),
                *(f"sec_timesfm_pred_return_{h}s" for h in HORIZONS)}
    missing = required - set(prediction_columns)
    if missing:
        raise ValueError(f"Prediction CSV missing columns: {sorted(missing)}")
    pred_ids = [row.get("event_id") for row in predictions]
    if any(not event_id for event_id in pred_ids) or len(pred_ids) != len(set(pred_ids)):
        raise ValueError("Prediction event IDs must be present and unique")
    actual_by_id = {row["event_id"]: row for row in actuals}
    if tuple(price_columns) != ("symbol", "session_date", "close_at_utc", "adjusted_close"):
        raise ValueError("Price CSV has unexpected columns")
    price_by_symbol_date = {(row["symbol"].upper(), row["session_date"]): row for row in price_rows}
    scored_at = _timestamp(scored_at_utc) if scored_at_utc else datetime.now(timezone.utc)
    joined = []
    not_yet_created_count = 0
    for prediction in predictions:
        event_id = prediction["event_id"]
        actual = actual_by_id.get(event_id)
        if actual is None:
            continue
        if prediction["issuer_cik"] != actual.get("issuer_cik"):
            raise ValueError(f"Issuer mismatch for prediction {event_id}")
        if prediction["forecast_origin_session_date"] != actual.get("forecast_origin_session_date"):
            raise ValueError(f"Forecast origin mismatch for prediction {event_id}")
        if _timestamp(prediction["event_available_at_utc"]) != _timestamp(actual["event_available_at_utc"]):
            raise ValueError(f"Event availability mismatch for prediction {event_id}")
        forecast_created = _timestamp(prediction["forecast_created_at_utc"])
        event_available = _timestamp(prediction["event_available_at_utc"])
        if forecast_created < event_available:
            raise ValueError(f"Prediction precedes event availability for {event_id}")
        if forecast_created > scored_at:
            not_yet_created_count += 1
            continue
        origin = price_by_symbol_date.get((prediction["issuer_symbol"].upper(),
                                           prediction["forecast_origin_session_date"]))
        if origin is None or _timestamp(origin["close_at_utc"]) != _timestamp(prediction["forecast_origin_close_at_utc"]):
            raise ValueError(f"Prediction origin does not match archived market data for {event_id}")
        if not math.isclose(float(origin["adjusted_close"]), float(prediction["forecast_origin_adjusted_close"]),
                            rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"Prediction origin close value mismatch for {event_id}")
        row = {**prediction, "forecast_status": actual.get("forecast_status", "")}
        for horizon in HORIZONS:
            suffix = f"{horizon}s"
            status = actual.get(f"status_{suffix}", "")
            end_date = actual.get(f"actual_end_session_date_{suffix}")
            end = price_by_symbol_date.get((prediction["issuer_symbol"].upper(), end_date)) if end_date else None
            if status == "OK":
                if end is None:
                    raise ValueError(f"Matured outcome has no archived end close for {event_id} {suffix}")
                if _timestamp(end["close_at_utc"]) > scored_at:
                    status = "OUTCOME_NOT_YET_MATURED"
            row[f"status_{suffix}"] = status
            row[f"actual_return_{suffix}"] = actual.get(f"actual_return_{suffix}")
        joined.append(row)
    if not joined:
        raise ValueError("No prediction events have matching realized-return rows")
    summary = {
        "schema_version": "paired-shadow-score-summary-v1",
        "prediction_count": len(predictions), "matched_outcome_count": len(joined),
        "predictions_created_after_score_time": not_yet_created_count,
        "matured_status_counts": {f"{h}s": {status: sum(row[f"status_{h}s"] == status for row in joined)
                                               for status in sorted({row[f"status_{h}s"] for row in joined})}
                                  for h in HORIZONS},
        "metrics": {f"{h}s": _loss_metrics(joined, h) for h in HORIZONS},
        "metrics_by_issuer": {f"{h}s": _grouped_loss_metrics(joined, h, "issuer_symbol") for h in HORIZONS},
        "metrics_by_transaction_code": {
            f"{h}s": _grouped_loss_metrics(joined, h, "transaction_code") for h in HORIZONS
        },
        "paired_mae_delta_95pct_cluster_ci": {
            f"{h}s": _cluster_ci(joined, h, replicates=replicates, seed=seed + h) for h in HORIZONS
        },
        "score_as_of_utc": _stamp(scored_at_utc),
        "price_snapshot_observed_at_utc": price_manifest.get("observed_at_utc"),
        "forecast_status": "RESEARCH_ONLY_NO_ORDERS",
    }
    stamp = _stamp(scored_at_utc)
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "paired-shadow-score-manifest-v1", "built_at_utc": stamp,
        "prediction_input": {"path": str(predictions_path), "sha256": _sha(predictions_path.read_bytes()),
                             "manifest_sha256": _sha(prediction_manifest_raw)},
        "actual_returns_input": {"path": str(actual_returns_path), "sha256": _sha(actual_returns_path.read_bytes()),
                                 "manifest_sha256": _sha(actual_manifest_raw)},
        "price_input": {"path": str(prices_path), "sha256": _sha(price_raw),
                        "manifest_path": str(price_manifest_path), "manifest_sha256": _sha(price_manifest_raw)},
        "bootstrap": {"method": "issuer-week cluster bootstrap", "replicates": replicates, "seed": seed},
        "summary_sha256": _sha(summary_path.read_bytes()),
        "research_only": True, "forecast_status": "RESEARCH_ONLY_NO_ORDERS",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "summary": summary, "manifest": manifest}
