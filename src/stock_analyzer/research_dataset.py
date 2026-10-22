"""Assemble event-level Form 4 features and return labels with provenance checks."""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .ledger import parse_timestamp, utc_text


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_csv(path: Path) -> tuple[bytes, list[str], list[dict]]:
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    columns = reader.fieldnames or []
    if not columns or len(columns) != len(set(columns)):
        raise ValueError(f"CSV must have unique named columns: {path}")
    return raw, columns, list(reader)


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_output(raw: bytes, rows: list[dict], manifest: dict, kind: str) -> None:
    output = manifest.get("output", {})
    if output.get("sha256") != _sha256(raw):
        raise ValueError(f"{kind} CSV hash does not match its manifest")
    if output.get("row_count") != len(rows):
        raise ValueError(f"{kind} CSV row count does not match its manifest")


def _unique_ids(rows: list[dict], kind: str) -> set[str]:
    ids = [row.get("event_id") for row in rows]
    if not ids or any(not value for value in ids):
        raise ValueError(f"{kind} rows must contain event IDs")
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate event IDs in {kind} CSV")
    return set(ids)


def _canonical_time(value: str) -> str:
    return utc_text(parse_timestamp(value))


def build_event_research_dataset(events_path: Path, financial_features_path: Path,
                                 labels_path: Path, output_root: Path, *,
                                 built_at_utc: str | None = None) -> dict:
    """Write one row per event, widening PIT financials and attaching outcomes.

    The inputs must all be manifest-backed outputs from the existing event,
    point-in-time financial-join, and return-label builders. All are checked
    against the same event CSV hash and the same event ID/public-time mapping.
    """
    events_path = Path(events_path)
    financial_features_path = Path(financial_features_path)
    labels_path = Path(labels_path)
    output_root = Path(output_root)

    event_manifest = _read_manifest(events_path.parent / "manifest.json")
    financial_manifest = _read_manifest(financial_features_path.parent / "manifest.json")
    label_manifest = _read_manifest(labels_path.parent / "manifest.json")
    event_raw, event_columns, events = _read_csv(events_path)
    financial_raw, financial_columns, financial_rows = _read_csv(financial_features_path)
    label_raw, label_columns, label_rows = _read_csv(labels_path)
    _verify_output(event_raw, events, event_manifest, "Event")
    _verify_output(financial_raw, financial_rows, financial_manifest, "Financial-feature")
    _verify_output(label_raw, label_rows, label_manifest, "Return-label")

    event_hash = _sha256(event_raw)
    if financial_manifest.get("event_input", {}).get("sha256") != event_hash:
        raise ValueError("Financial join was built from a different event CSV")
    if label_manifest.get("event_input", {}).get("sha256") != event_hash:
        raise ValueError("Return labels were built from a different event CSV")

    event_ids = _unique_ids(events, "Event")
    financial_ids = {row.get("event_id") for row in financial_rows}
    label_ids = _unique_ids(label_rows, "Return-label")
    if financial_ids != event_ids:
        raise ValueError("Financial feature event IDs do not match the event dataset")
    if label_ids != event_ids:
        raise ValueError("Return-label event IDs do not match the event dataset")

    events_by_id = {row["event_id"]: row for row in events}
    labels_by_id = {row["event_id"]: row for row in label_rows}
    financial_by_event: dict[str, dict[str, dict]] = {event_id: {} for event_id in event_ids}
    metrics: list[str] = []
    financial_fields = [field for field in financial_columns
                        if field not in ("event_id", "issuer_cik", "event_available_at_utc", "metric")]
    for row in financial_rows:
        event_id, metric = row.get("event_id"), row.get("metric")
        if event_id not in event_ids or not metric:
            raise ValueError("Financial rows must have a known event ID and metric")
        if metric in financial_by_event[event_id]:
            raise ValueError(f"Duplicate financial metric {metric} for event {event_id}")
        if metric not in metrics:
            metrics.append(metric)
        event_time = parse_timestamp(events_by_id[event_id]["historical_available_after_utc"])
        if _canonical_time(row.get("event_available_at_utc") or "") != _canonical_time(
                events_by_id[event_id]["historical_available_after_utc"]):
            raise ValueError(f"Financial feature timestamp mismatch for event {event_id}")
        status = row.get("selection_status")
        if status == "SELECTED_AS_OF_EVENT":
            available = row.get("fact_available_after_utc")
            if not available or parse_timestamp(available) > event_time:
                raise ValueError(f"Future or missing financial availability for event {event_id}")
            if row.get("value") in (None, ""):
                raise ValueError(f"Selected financial fact has no value for event {event_id}")
        elif status != "NO_ELIGIBLE_FACT_AS_OF_EVENT":
            raise ValueError(f"Unrecognized point-in-time selection status: {status}")
        financial_by_event[event_id][metric] = row

    metric_sets = {event_id: set(by_metric) for event_id, by_metric in financial_by_event.items()}
    if not metrics or any(values != set(metrics) for values in metric_sets.values()):
        raise ValueError("Every event must have the same nonempty set of financial metrics")

    for row in label_rows:
        event_id = row["event_id"]
        if _canonical_time(row.get("event_available_at_utc") or "") != _canonical_time(
                events_by_id[event_id]["historical_available_after_utc"]):
            raise ValueError(f"Return-label timestamp mismatch for event {event_id}")

    label_fields = [field for field in label_columns if field != "event_id"]
    output_columns = list(event_columns)
    output_columns += [f"financial_{metric}_{field}" for metric in metrics for field in financial_fields]
    output_columns += [f"label_{field}" for field in label_fields]
    if len(output_columns) != len(set(output_columns)):
        raise ValueError("Output feature names collide")
    output_rows = []
    for event in events:
        event_id = event["event_id"]
        output = dict(event)
        for metric in metrics:
            feature = financial_by_event[event_id][metric]
            for field in financial_fields:
                output[f"financial_{metric}_{field}"] = feature.get(field)
        output.update({f"label_{field}": value for field, value in labels_by_id[event_id].items()
                       if field != "event_id"})
        output_rows.append(output)

    stamp = _canonical_time(built_at_utc) if built_at_utc else utc_text(datetime.now(timezone.utc))
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output_path = folder / "event_research_dataset.csv"
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=output_columns, extrasaction="raise")
        writer.writeheader()
        writer.writerows(output_rows)
    summary = {
        "schema_version": "form4-event-research-dataset-summary-v1",
        "event_count": len(output_rows),
        "financial_metric_count": len(metrics),
        "financial_metrics": metrics,
        "selected_financial_fact_rows": sum(
            row.get("selection_status") == "SELECTED_AS_OF_EVENT" for row in financial_rows),
        "missing_financial_fact_rows": sum(
            row.get("selection_status") == "NO_ELIGIBLE_FACT_AS_OF_EVENT" for row in financial_rows),
        "primary_sample_event_count": sum(
            row.get("primary_sample_eligible") == "true" for row in label_rows),
        "return_label_status_counts": {
            field: {status: sum(row.get(field) == status for row in label_rows)
                    for status in sorted({row.get(field) for row in label_rows})}
            for field in ("label_status_1d", "label_status_5d", "label_status_20d")
            if field in label_columns
        },
        "forecast_status": "LABELS_AND_FEATURES_NO_FORECAST",
    }
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "form4-event-research-dataset-manifest-v1",
        "built_at_utc": stamp,
        "event_input": {"path": str(events_path), "sha256": event_hash},
        "financial_features_input": {"path": str(financial_features_path),
                                     "sha256": _sha256(financial_raw)},
        "return_labels_input": {"path": str(labels_path), "sha256": _sha256(label_raw)},
        "output": {"path": str(output_path), "sha256": _sha256(output_path.read_bytes()),
                   "row_count": len(output_rows)},
        "summary_sha256": _sha256(summary_path.read_bytes()),
        "join_key": "event_id",
        "financial_availability_policy": "selected financial facts must be available by event historical availability",
        "forecast_status": "LABELS_AND_FEATURES_NO_FORECAST",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    return {"output_dir": folder, "csv_path": output_path, "summary": summary, "manifest": manifest}
