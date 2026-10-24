"""Post-hoc coverage and subgroup diagnostics for SEC signal evaluations."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .ledger import parse_timestamp, utc_text
from .sec_signal_models import _split_rows

MODELS = (
    "price_only", "price_plus_insider", "price_plus_financial", "price_plus_10k",
    "price_plus_10q", "price_plus_insider_10k", "price_plus_insider_10q", "price_plus_all_sec",
)
FAMILIES = ("10k", "10q")


def _read_csv(path: Path) -> tuple[bytes, list[dict[str, str]]]:
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    fields = reader.fieldnames or []
    if not fields or len(fields) != len(set(fields)):
        raise ValueError(f"CSV must have unique named columns: {path}")
    return raw, list(reader)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _number(value: str | None) -> float | None:
    try:
        result = float(value or "")
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _metric_stats(rows: list[dict], model: str) -> dict:
    pairs = [(_number(row.get("actual_excess_return")), _number(row.get(f"prediction_{model}")))
             for row in rows]
    pairs = [(actual, predicted) for actual, predicted in pairs
             if actual is not None and predicted is not None]
    errors = [pred - actual for actual, pred in pairs]
    return {
        "count": len(pairs),
        "mae": sum(abs(error) for error in errors) / len(errors) if errors else None,
        "rmse": math.sqrt(sum(error * error for error in errors) / len(errors)) if errors else None,
        "mean_error": sum(errors) / len(errors) if errors else None,
    }


def _group_key(row: dict, group_by: str) -> str:
    if group_by == "issuer":
        return (row.get("issuer_symbol") or "UNKNOWN").strip().upper()
    if group_by == "year":
        event = row.get("_event", {})
        filing_date = event.get("filing_date", "")
        if len(filing_date) >= 4 and filing_date[:4].isdigit():
            return filing_date[:4]
        available = parse_timestamp(row["historical_available_after_utc"])
        return str(available.year)
    raise ValueError("group_by must be 'issuer' or 'year'")


def diagnose_sec_signal_evaluation(evaluation_run: Path, research_dataset: Path,
                                   output_root: Path, *, built_at_utc: str | None = None) -> dict:
    evaluation_run, research_dataset, output_root = map(Path, (evaluation_run, research_dataset, output_root))
    eval_manifest_path = evaluation_run / "manifest.json"
    eval_manifest = json.loads(eval_manifest_path.read_text(encoding="utf-8"))
    predictions_path = evaluation_run / "sec_signal_holdout_predictions.csv"
    predictions_raw, predictions = _read_csv(predictions_path)
    if _sha(predictions_raw) != eval_manifest.get("outputs", {}).get("predictions", {}).get("sha256"):
        raise ValueError("Holdout prediction CSV does not match evaluation manifest")
    research_raw, research_rows = _read_csv(research_dataset)
    research_manifest_path = research_dataset.parent / "manifest.json"
    research_manifest = json.loads(research_manifest_path.read_text(encoding="utf-8"))
    output_record = research_manifest.get("output", {})
    if _sha(research_raw) != output_record.get("sha256"):
        raise ValueError("Research dataset does not match its manifest")
    expected_dataset = eval_manifest.get("research_dataset", {}).get("sha256")
    if expected_dataset and expected_dataset != _sha(research_raw):
        raise ValueError("Research dataset differs from the one used by this evaluation")

    by_event = {row.get("event_id"): row for row in research_rows}
    if len(by_event) != len(research_rows) or "" in by_event:
        raise ValueError("Research dataset requires unique, nonempty event IDs")
    joined = []
    for prediction in predictions:
        event_id = prediction.get("event_id")
        event = by_event.get(event_id)
        if event is None:
            raise ValueError(f"Holdout event missing from research dataset: {event_id}")
        if event.get("historical_available_after_utc") != prediction.get("historical_available_after_utc"):
            raise ValueError(f"Availability mismatch for holdout event {event_id}")
        joined.append({**prediction, "_event": event})

    split_checks = {}
    eval_summary = json.loads((evaluation_run / "summary.json").read_text(encoding="utf-8"))
    train_fraction = 1.0 - float(eval_summary.get("model", {}).get(
        "holdout_fraction_by_unique_event_date", 0.2
    ))
    for horizon in sorted({int(row["horizon_sessions"]) for row in joined}):
        sample = [row for row in joined if int(row["horizon_sessions"]) == horizon]
        cutoff = sample[0]["holdout_cutoff_date"]
        if any(row["holdout_cutoff_date"] != cutoff for row in sample):
            raise ValueError(f"Inconsistent holdout cutoffs for {horizon}-session horizon")
        held_out_start = min(row.get("label_start_session_date", "") for row in sample)
        label_ends = [row.get("label_end_session_date", "") for row in sample]
        train_idx, expected_test_idx, recomputed_cutoff = _split_rows(
            research_rows, horizon, train_fraction=train_fraction,
            minimum_training_rows=3, minimum_test_rows=2,
        )
        actual_test_ids = {row["event_id"] for row in sample}
        expected_test_ids = {research_rows[index]["event_id"] for index in expected_test_idx}
        first_test_start = min(research_rows[index].get("label_start_session_date", "")
                               for index in expected_test_idx)
        train_outcome_purged = all(
            (research_rows[index].get(f"label_end_session_date_{horizon}d") or "") < first_test_start
            for index in train_idx
        )
        recorded_horizon = eval_summary.get("horizons", {}).get(f"{horizon}s", {})
        split_checks[str(horizon)] = {
            "cutoff_date": cutoff,
            "recomputed_cutoff_date": recomputed_cutoff,
            "holdout_count": len(sample),
            "expected_holdout_count": len(expected_test_idx),
            "holdout_ids_match_recomputed_chronological_split": actual_test_ids == expected_test_ids,
            "cutoff_matches_evaluation_summary": cutoff == recorded_horizon.get("chronological_holdout_cutoff_date"),
            "holdout_count_matches_evaluation_summary": len(sample) == recorded_horizon.get("holdout_event_count"),
            "all_holdout_availability_on_or_after_cutoff": all(
                parse_timestamp(row["historical_available_after_utc"]).date().isoformat() >= cutoff
                for row in sample
            ),
            "earliest_holdout_start_session": held_out_start,
            "latest_holdout_end_session": max(label_ends) if label_ends else None,
            "training_count_recomputed_after_purge": len(train_idx),
            "training_outcomes_end_before_first_holdout_start": train_outcome_purged,
        }

    metric_rows = []
    coverage_rows = []
    for horizon in sorted({int(row["horizon_sessions"]) for row in joined}):
        horizon_rows = [row for row in joined if int(row["horizon_sessions"]) == horizon]
        for dimension in ("issuer", "year"):
            groups: dict[str, list[dict]] = {}
            for row in horizon_rows:
                groups.setdefault(_group_key(row, dimension), []).append(row)
            for key, group in sorted(groups.items()):
                for model in MODELS:
                    stats = _metric_stats(group, model)
                    metric_rows.append({"horizon_sessions": horizon, "group_by": dimension,
                                        "group": key, "model": model, **stats})
                for family in FAMILIES:
                    metrics = sorted({field[len("financial_"):-len(f"_{family}_selection_status")]
                                      for field in group[0]["_event"]
                                      if field.startswith("financial_") and field.endswith(f"_{family}_selection_status")})
                    selected_slots = sum(
                        event.get(f"financial_{metric}_{family}_selection_status") == "SELECTED_AS_OF_EVENT"
                        for row in group for metric in metrics for event in [row["_event"]]
                    )
                    slot_count = len(group) * len(metrics)
                    events_with_any = sum(any(
                        row["_event"].get(f"financial_{metric}_{family}_selection_status") == "SELECTED_AS_OF_EVENT"
                        for metric in metrics
                    ) for row in group)
                    coverage_rows.append({
                        "horizon_sessions": horizon, "group_by": dimension, "group": key,
                        "event_count": len(group), "family": family, "metric_count": len(metrics),
                        "selected_fact_slots": selected_slots, "possible_fact_slots": slot_count,
                        "selected_slot_rate": selected_slots / slot_count if slot_count else None,
                        "events_with_any_selected_fact": events_with_any,
                        "event_coverage_rate": events_with_any / len(group) if group else None,
                    })

    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    out_dir = output_root / stamp.replace(":", "-")
    out_dir.mkdir(parents=True, exist_ok=False)
    metrics_path = out_dir / "holdout_metrics_by_issuer_year.csv"
    with metrics_path.open("w", encoding="utf-8", newline="") as stream:
        fields = ["horizon_sessions", "group_by", "group", "model", "count", "mae", "rmse", "mean_error"]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(metric_rows)
    coverage_path = out_dir / "holdout_financial_coverage_by_issuer_year.csv"
    with coverage_path.open("w", encoding="utf-8", newline="") as stream:
        fields = ["horizon_sessions", "group_by", "group", "event_count", "family", "metric_count",
                  "selected_fact_slots", "possible_fact_slots", "selected_slot_rate",
                  "events_with_any_selected_fact", "event_coverage_rate"]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(coverage_rows)
    summary = {
        "schema_version": "sec-signal-diagnostics-summary-v1",
        "forecast_status": "RESEARCH_ONLY_POST_HOC_DIAGNOSTICS",
        "holdout_event_count": len(joined),
        "unique_holdout_events": len({row["event_id"] for row in joined}),
        "split_checks": split_checks,
        "interpretation_note": "Small subgroup metrics are descriptive and should not be treated as evidence of generalizable predictive value.",
        "outputs": {"metrics": str(metrics_path), "financial_coverage": str(coverage_path)},
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": out_dir, "summary": summary}
