"""Leakage-aware, research-only SEC feature model comparisons."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random

from .ledger import parse_timestamp, utc_text

HORIZONS = (5, 20)
MODEL_GROUPS = {
    "price_only": ("price",),
    "price_plus_insider": ("price", "insider"),
    "price_plus_financial": ("price", "financial"),
    "price_plus_all_sec": ("price", "insider", "financial"),
}
PRICE_COLUMNS = ("symbol", "session_date", "close_at_utc", "adjusted_close")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _csv(path: Path) -> tuple[bytes, list[str], list[dict[str, str]]]:
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    fields = reader.fieldnames or []
    if not fields or len(fields) != len(set(fields)):
        raise ValueError(f"CSV must have unique named columns: {path}")
    return raw, fields, list(reader)


def _verify_artifact(path: Path, raw: bytes, rows: list[dict], manifest: dict) -> None:
    output = manifest.get("output", {})
    if output.get("sha256") != _sha(raw):
        raise ValueError(f"CSV hash does not match its manifest: {path}")
    if output.get("row_count") != len(rows):
        raise ValueError(f"CSV row count does not match its manifest: {path}")


def _bool(value: str | None) -> bool | None:
    text = (value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _finite(value: str | None) -> float | None:
    try:
        result = float(value or "")
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _signed_log(value: float) -> float:
    return math.copysign(math.log1p(abs(value)), value)


def _read_price_history(prices_path: Path, price_manifest_path: Path) -> tuple[dict[str, list[dict]], dict]:
    raw, fields, rows = _csv(prices_path)
    if tuple(fields) != PRICE_COLUMNS:
        raise ValueError(f"Price CSV columns must be exactly: {', '.join(PRICE_COLUMNS)}")
    manifest = _json(price_manifest_path)
    if manifest.get("csv", {}).get("sha256") != _sha(raw):
        raise ValueError("Price CSV hash does not match its snapshot manifest")
    if manifest.get("row_count", len(rows)) != len(rows):
        raise ValueError("Price CSV row count does not match its snapshot manifest")
    grouped: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        symbol = (row.get("symbol") or "").strip().upper()
        day = (row.get("session_date") or "").strip()
        close_time = parse_timestamp(row.get("close_at_utc") or "")
        close = _finite(row.get("adjusted_close"))
        if not symbol or not day or close is None or close <= 0:
            raise ValueError("Price rows require symbol, session date, and positive adjusted close")
        if (symbol, day) in seen:
            raise ValueError(f"Duplicate price row: {symbol} {day}")
        seen.add((symbol, day))
        grouped.setdefault(symbol, []).append({"session_date": day, "close_at": close_time, "close": close})
    for history in grouped.values():
        history.sort(key=lambda row: row["session_date"])
    return grouped, manifest


def _price_features(event: dict, histories: dict[str, list[dict]], benchmark: str) -> dict[str, float]:
    event_time = parse_timestamp(event["historical_available_after_utc"])
    symbol = (event.get("issuer_symbol") or "").strip().upper()
    result: dict[str, float] = {}
    for prefix, ticker in (("issuer", symbol), ("benchmark", benchmark)):
        history = [row for row in histories.get(ticker, []) if row["close_at"] < event_time]
        result[f"price.{prefix}.history_available"] = float(bool(history))
        if not history:
            continue
        closes = [row["close"] for row in history]
        for sessions in (1, 5, 20):
            if len(closes) > sessions:
                result[f"price.{prefix}.return_{sessions}s"] = closes[-1] / closes[-1 - sessions] - 1.0
        if len(closes) >= 21:
            daily = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - 20, len(closes))]
            mean = sum(daily) / len(daily)
            result[f"price.{prefix}.volatility_20s"] = math.sqrt(
                sum((item - mean) ** 2 for item in daily) / (len(daily) - 1)
            )
    issuer_20 = result.get("price.issuer.return_20s")
    benchmark_20 = result.get("price.benchmark.return_20s")
    if issuer_20 is not None and benchmark_20 is not None:
        result["price.relative_return_20s"] = issuer_20 - benchmark_20
    return result


def _event_features(row: dict[str, str], fields: list[str], event_time: datetime) -> dict[str, dict[str, float | str]]:
    groups: dict[str, dict[str, float | str]] = {name: {} for name in ("insider", "financial")}
    for name in ("transaction_code", "transaction_class", "acquired_disposed", "ownership_type"):
        value = (row.get(name) or "").strip()
        if value and value.lower() not in {"unknown", "none", "null"}:
            groups["insider"][f"insider.{name}"] = value.upper()
    for name in ("is_derivative", "is_amendment", "aff10b5one"):
        value = _bool(row.get(name))
        if value is not None:
            groups["insider"][f"insider.{name}"] = float(value)
    for name in ("shares", "price_per_share", "reported_gross_value", "shares_owned_after"):
        value = _finite(row.get(name))
        if value is not None:
            groups["insider"][f"insider.{name}"] = _signed_log(value)

    metrics = sorted({field[len("financial_"):-len("_value")]
                      for field in fields if field.startswith("financial_") and field.endswith("_value")})
    for metric in metrics:
        selection = row.get(f"financial_{metric}_selection_status")
        value = _finite(row.get(f"financial_{metric}_value"))
        available = row.get(f"financial_{metric}_fact_available_after_utc")
        if selection == "SELECTED_AS_OF_EVENT":
            if not available or parse_timestamp(available) > event_time:
                raise ValueError(f"Financial feature {metric} was not available by its event time")
            if value is None:
                raise ValueError(f"Selected financial feature {metric} has no numeric value")
            groups["financial"][f"financial.{metric}.signed_log_value"] = _signed_log(value)
            groups["financial"][f"financial.{metric}.available"] = 1.0
        elif selection == "NO_ELIGIBLE_FACT_AS_OF_EVENT":
            groups["financial"][f"financial.{metric}.available"] = 0.0
        elif selection:
            raise ValueError(f"Unrecognized selection status for financial feature {metric}: {selection}")
    return groups


def _rows_to_vectors(rows: list[dict[str, str]], fields: list[str], histories: dict,
                     benchmark: str) -> list[dict[str, float | str]]:
    vectors = []
    for row in rows:
        event_time_text = row.get("historical_available_after_utc")
        if not event_time_text:
            raise ValueError("Event row missing historical_available_after_utc")
        event_time = parse_timestamp(event_time_text)
        vector: dict[str, float | str] = {}
        groups = _event_features(row, fields, event_time)
        vector.update(_price_features(row, histories, benchmark))
        for group in ("insider", "financial"):
            vector.update(groups[group])
        vectors.append(vector)
    return vectors


def _metrics(actual: list[float], predicted: list[float]) -> dict:
    if not actual:
        return {"count": 0, "mae": None, "rmse": None, "mean_error": None,
                "directional_accuracy": None, "spearman_ic": None}
    errors = [pred - truth for truth, pred in zip(actual, predicted)]
    mae = sum(abs(error) for error in errors) / len(errors)
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
    directional = sum((truth > 0) == (pred > 0) for truth, pred in zip(actual, predicted)) / len(actual)
    try:
        from scipy.stats import spearmanr
        if len(set(actual)) < 2 or len(set(predicted)) < 2:
            corr = None
        else:
            corr = spearmanr(actual, predicted).statistic
            corr = float(corr) if math.isfinite(float(corr)) else None
    except (ImportError, ValueError, TypeError):
        corr = None
    return {"count": len(actual), "mae": mae, "rmse": rmse,
            "mean_error": sum(errors) / len(errors), "directional_accuracy": directional,
            "spearman_ic": corr, "zero_return_mae": sum(abs(x) for x in actual) / len(actual),
            "zero_return_rmse": math.sqrt(sum(x * x for x in actual) / len(actual))}


def _paired_cluster_ci(rows: list[dict], left: str, right: str, *, replicates: int, seed: int) -> dict:
    """Bootstrap paired MAE improvement by issuer-week clusters."""
    if not rows:
        return {"clusters": 0, "replicates": 0, "lower_95": None, "upper_95": None}
    clusters: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        event_time = parse_timestamp(row["historical_available_after_utc"])
        iso = event_time.date().isocalendar()
        key = (row.get("issuer_cik") or row.get("issuer_symbol") or "", f"{iso.year}-W{iso.week:02d}")
        clusters.setdefault(key, []).append(row)
    keys = list(clusters)
    if len(keys) < 2 or replicates <= 0:
        return {"clusters": len(keys), "replicates": 0, "lower_95": None, "upper_95": None}
    rng = random.Random(seed)
    differences = []
    for _ in range(replicates):
        chosen = [rng.choice(keys) for _ in keys]
        sample = [item for key in chosen for item in clusters[key]]
        difference = sum(abs(item[left] - item["actual"]) - abs(item[right] - item["actual"])
                         for item in sample) / len(sample)
        differences.append(difference)
    differences.sort()
    return {"clusters": len(keys), "replicates": replicates,
            "lower_95": differences[int(0.025 * (replicates - 1))],
            "upper_95": differences[int(0.975 * (replicates - 1))]}


def _split_rows(rows: list[dict[str, str]], horizon: int, *, train_fraction: float,
                minimum_training_rows: int, minimum_test_rows: int) -> tuple[list[int], list[int], str]:
    eligible = []
    status_field = f"label_label_status_{horizon}d"
    actual_field = f"label_excess_return_{horizon}d"
    for index, row in enumerate(rows):
        if _bool(row.get("label_primary_sample_eligible")) is not True:
            continue
        if row.get(status_field) != "OK" or _finite(row.get(actual_field)) is None:
            continue
        eligible.append(index)
    if len(eligible) < minimum_training_rows + minimum_test_rows:
        raise ValueError(f"Horizon {horizon}s has too few eligible events for chronological evaluation")
    dates = sorted({parse_timestamp(rows[i]["historical_available_after_utc"]).date().isoformat()
                    for i in eligible})
    if len(dates) < 3:
        raise ValueError(f"Horizon {horizon}s needs at least three distinct event dates")
    split_pos = max(1, min(len(dates) - 1, math.floor(len(dates) * train_fraction)))
    cutoff = dates[split_pos]
    test_indices = [i for i in eligible
                    if parse_timestamp(rows[i]["historical_available_after_utc"]).date().isoformat() >= cutoff]
    train_candidates = [i for i in eligible
                        if parse_timestamp(rows[i]["historical_available_after_utc"]).date().isoformat() < cutoff]
    if not test_indices:
        raise ValueError(f"Horizon {horizon}s produced an empty chronological holdout")
    first_test_start = min(rows[i].get("label_start_session_date", "") for i in test_indices)
    train_indices = [i for i in train_candidates
                     if (rows[i].get(f"label_end_session_date_{horizon}d") or "") < first_test_start]
    if len(train_indices) < minimum_training_rows or len(test_indices) < minimum_test_rows:
        raise ValueError(
            f"Horizon {horizon}s split too small after purging overlapping outcomes "
            f"(train={len(train_indices)}, test={len(test_indices)}, cutoff={cutoff})"
        )
    return train_indices, test_indices, cutoff


def evaluate_sec_signal_models(dataset_path: Path, prices_path: Path, price_manifest_path: Path,
                               output_root: Path, *, train_fraction: float = 0.8,
                               ridge_alpha: float = 10.0, minimum_training_rows: int = 40,
                               minimum_test_rows: int = 20, bootstrap_replicates: int = 1000,
                               primary_start_year: int = 2010, built_at_utc: str | None = None) -> dict:
    """Compare price-only and SEC feature Ridge models on a purged time holdout."""
    if not 0.5 <= train_fraction <= 0.9:
        raise ValueError("train_fraction must be between 0.5 and 0.9")
    if ridge_alpha <= 0 or minimum_training_rows < 3 or minimum_test_rows < 2:
        raise ValueError("Invalid model or sample-size configuration")
    dataset_path, prices_path = Path(dataset_path), Path(prices_path)
    price_manifest_path, output_root = Path(price_manifest_path), Path(output_root)
    dataset_raw, fields, rows = _csv(dataset_path)
    dataset_manifest_path = dataset_path.parent / "manifest.json"
    dataset_manifest = _json(dataset_manifest_path)
    _verify_artifact(dataset_path, dataset_raw, rows, dataset_manifest)
    if dataset_manifest.get("forecast_status") != "LABELS_AND_FEATURES_NO_FORECAST":
        raise ValueError("Dataset manifest is not an eligible unmodeled research artifact")

    labels_ref = dataset_manifest.get("return_labels_input", {})
    labels_path = Path(labels_ref.get("path", ""))
    if not labels_path.is_absolute():
        labels_path = Path.cwd() / labels_path
    labels_raw = labels_path.read_bytes()
    if _sha(labels_raw) != labels_ref.get("sha256"):
        raise ValueError("Return-label input hash does not match the research manifest")
    labels_manifest = _json(labels_path.parent / "manifest.json")
    if labels_manifest.get("output", {}).get("sha256") != _sha(labels_raw):
        raise ValueError("Return-label CSV hash does not match its manifest")
    histories, price_manifest = _read_price_history(prices_path, price_manifest_path)
    expected_price_hash = labels_manifest.get("price_input", {}).get("sha256")
    if expected_price_hash != _sha(prices_path.read_bytes()):
        raise ValueError("Evaluation prices must be the exact price snapshot used for labels")
    if price_manifest.get("csv", {}).get("sha256") != expected_price_hash:
        raise ValueError("Price snapshot manifest and label manifest disagree")

    event_ids = [row.get("event_id") for row in rows]
    if not event_ids or any(not event_id for event_id in event_ids) or len(event_ids) != len(set(event_ids)):
        raise ValueError("Research dataset must have unique, nonempty event IDs")
    for row in rows:
        if not row.get("historical_available_after_utc"):
            raise ValueError("Event row missing historical_available_after_utc")
        if row.get("label_event_available_at_utc"):
            if utc_text(parse_timestamp(row["label_event_available_at_utc"])) != utc_text(
                    parse_timestamp(row["historical_available_after_utc"])):
                raise ValueError(f"Label/event availability mismatch: {row['event_id']}")

    benchmark_values = {(row.get("label_benchmark_symbol") or "SPY").strip().upper() for row in rows}
    if len(benchmark_values) != 1:
        raise ValueError("Research dataset must use a single benchmark")
    benchmark = next(iter(benchmark_values))
    vectors = _rows_to_vectors(rows, fields, histories, benchmark)

    try:
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
    except ImportError as error:
        raise RuntimeError('Install research dependencies with: python -m pip install -e ".[research]"') from error

    horizons_summary: dict[str, dict] = {}
    prediction_rows: list[dict] = []
    coefficient_rows: list[dict] = []
    for horizon in HORIZONS:
        train_idx, test_idx, cutoff = _split_rows(
            rows, horizon, train_fraction=train_fraction,
            minimum_training_rows=minimum_training_rows, minimum_test_rows=minimum_test_rows,
        )
        actual_field = f"label_excess_return_{horizon}d"
        train_y = [_finite(rows[i][actual_field]) for i in train_idx]
        test_y = [_finite(rows[i][actual_field]) for i in test_idx]
        assert all(value is not None for value in train_y + test_y)
        actual = [float(value) for value in test_y if value is not None]
        predictions: dict[str, list[float]] = {}
        feature_importance: dict[str, list[dict]] = {}
        for model_name, groups in MODEL_GROUPS.items():
            allowed = set(groups)
            x_train_dict = [{key: value for key, value in vectors[i].items()
                             if key.split(".", 1)[0] in allowed} for i in train_idx]
            x_test_dict = [{key: value for key, value in vectors[i].items()
                            if key.split(".", 1)[0] in allowed} for i in test_idx]
            vectorizer = DictVectorizer(sparse=True, sort=True)
            x_train = vectorizer.fit_transform(x_train_dict)
            x_test = vectorizer.transform(x_test_dict)
            scaler = StandardScaler(with_mean=False)
            x_train = scaler.fit_transform(x_train)
            x_test = scaler.transform(x_test)
            model = Ridge(alpha=ridge_alpha)
            model.fit(x_train, train_y)
            predicted = [float(value) for value in model.predict(x_test)]
            predictions[model_name] = predicted
            weights = model.coef_.ravel()
            names = vectorizer.get_feature_names_out()
            top = sorted(({"feature": str(name), "standardized_coefficient": float(weight)}
                          for name, weight in zip(names, weights)),
                         key=lambda item: abs(item["standardized_coefficient"]), reverse=True)[:30]
            feature_importance[model_name] = top
            coefficient_rows.extend({"horizon_sessions": horizon, "model": model_name, **item} for item in top)

        test_records = []
        for pos, index in enumerate(test_idx):
            row = rows[index]
            record = {"event_id": row["event_id"], "issuer_cik": row.get("issuer_cik"),
                      "issuer_symbol": row.get("issuer_symbol"),
                      "historical_available_after_utc": row["historical_available_after_utc"],
                      "label_start_session_date": row.get("label_start_session_date"),
                      "label_end_session_date": row.get(f"label_end_session_date_{horizon}d"),
                      "actual_excess_return": actual[pos], "horizon_sessions": horizon,
                      "holdout_cutoff_date": cutoff}
            for model_name in MODEL_GROUPS:
                record[f"prediction_{model_name}"] = predictions[model_name][pos]
            prediction_rows.append(record)
            test_records.append({**row, "actual": actual[pos], **{
                model_name: predictions[model_name][pos] for model_name in MODEL_GROUPS
            }})

        metrics = {name: _metrics(actual, predicted) for name, predicted in predictions.items()}
        metrics["zero_excess_return"] = _metrics(actual, [0.0] * len(actual))
        baseline = metrics["price_only"]
        for model_name in MODEL_GROUPS:
            metrics[model_name]["mae_delta_vs_price_only"] = (
                metrics[model_name]["mae"] - baseline["mae"]
            )
            metrics[model_name]["rmse_delta_vs_price_only"] = (
                metrics[model_name]["rmse"] - baseline["rmse"]
            )
        confidence = {
            model_name: _paired_cluster_ci(test_records, "price_only", model_name,
                                           replicates=bootstrap_replicates,
                                           seed=20261024 + horizon + index)
            for index, model_name in enumerate(MODEL_GROUPS) if model_name != "price_only"
        }
        horizons_summary[f"{horizon}s"] = {
            "target": "benchmark-adjusted issuer simple return",
            "chronological_holdout_cutoff_date": cutoff,
            "training_event_count_after_purge": len(train_idx),
            "holdout_event_count": len(test_idx),
            "training_availability_range": [min(rows[i]["historical_available_after_utc"] for i in train_idx),
                                             max(rows[i]["historical_available_after_utc"] for i in train_idx)],
            "holdout_availability_range": [min(rows[i]["historical_available_after_utc"] for i in test_idx),
                                            max(rows[i]["historical_available_after_utc"] for i in test_idx)],
            "metrics": metrics,
            "paired_mae_delta_vs_price_only_cluster_bootstrap_95pct": confidence,
            "top_standardized_coefficients": feature_importance,
        }

    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    out_dir = output_root / stamp.replace(":", "-")
    out_dir.mkdir(parents=True, exist_ok=False)
    predictions_path = out_dir / "sec_signal_holdout_predictions.csv"
    with predictions_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(prediction_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(prediction_rows)
    coefficients_path = out_dir / "standardized_coefficients_top30.csv"
    with coefficients_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["horizon_sessions", "model", "feature", "standardized_coefficient"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(coefficient_rows)
    summary = {
        "schema_version": "sec-signal-model-evaluation-summary-v1",
        "forecast_status": "RESEARCH_ONLY_SELECTED_COHORT_EXPLORATORY",
        "training_scope": "historical_form4_and_point_in_time_financial_features",
        "not_yet_included": ["8-K section features", "10-K/10-Q narrative features", "13D/13G", "13F"],
        "event_count": len(rows), "eligible_horizons": [5, 20],
        "primary_metrics": ["MAE", "Spearman IC", "paired issuer-week bootstrap MAE difference"],
        "model": {"estimator": "Ridge regression", "alpha": ridge_alpha,
                  "features_standardized_on_training_rows_only": True,
                  "holdout_fraction_by_unique_event_date": 1 - train_fraction},
        "feature_group_ablation_rule": "same eligible rows and chronological holdout for each model; compare nested fixed feature groups",
        "outcome_overlap_policy": "training outcomes must end before earliest holdout start session",
        "price_feature_availability_policy": "only adjusted closes with actual close_at_utc strictly before event availability",
        "price_adjustment_caveat": "adjusted-close history comes from a later acquired snapshot and may reflect later corporate-action adjustments",
        "horizons": horizons_summary,
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "sec-signal-model-evaluation-manifest-v1", "built_at_utc": stamp,
        "research_dataset": {"path": str(dataset_path), "sha256": _sha(dataset_raw),
                             "manifest_sha256": _sha(dataset_manifest_path.read_bytes())},
        "price_snapshot": {"path": str(prices_path), "sha256": _sha(prices_path.read_bytes()),
                           "manifest_path": str(price_manifest_path),
                           "manifest_sha256": _sha(price_manifest_path.read_bytes()),
                           "observed_at_utc": price_manifest.get("observed_at_utc")},
        "outputs": {
            "predictions": {"path": str(predictions_path), "sha256": _sha(predictions_path.read_bytes()),
                            "row_count": len(prediction_rows)},
            "coefficients": {"path": str(coefficients_path), "sha256": _sha(coefficients_path.read_bytes()),
                             "row_count": len(coefficient_rows)},
            "summary": {"path": str(summary_path), "sha256": _sha(summary_path.read_bytes())},
        },
        "input_event_hash": dataset_manifest.get("event_input", {}).get("sha256"),
        "feature_groups": MODEL_GROUPS,
        "primary_start_year": primary_start_year,
        "limitations": [
            "The current pilot cohort was selected from a 2026 S&P 500 snapshot; findings are not broad-market estimates.",
            "This first model version uses Form 4 and point-in-time financial features only.",
            "Standardized coefficients are model-specific and are not causal or universal feature weights.",
        ],
        "research_only": True,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": out_dir, "summary": summary, "manifest": manifest}
