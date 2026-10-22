"""Join experimental TimesFM forecasts to the manifested event research table."""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .ledger import parse_timestamp, utc_text


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_csv(path: Path) -> tuple[bytes, list[str], list[dict]]:
    raw = Path(path).read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    columns = reader.fieldnames or []
    if not columns or len(columns) != len(set(columns)):
        raise ValueError(f"CSV must have unique named columns: {path}")
    return raw, columns, list(reader)


def _manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _check_output(raw: bytes, rows: list[dict], manifest: dict, label: str) -> None:
    output = manifest.get("output", {})
    if output.get("sha256") != _sha256(raw):
        raise ValueError(f"{label} CSV hash does not match its manifest")
    if output.get("row_count") != len(rows):
        raise ValueError(f"{label} CSV row count does not match its manifest")


def _unique(rows: list[dict], label: str) -> set[str]:
    ids = [row.get("event_id") for row in rows]
    if not ids or any(not value for value in ids):
        raise ValueError(f"{label} rows must contain event IDs")
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate event IDs in {label} CSV")
    return set(ids)


def join_timesfm_research_dataset(research_path: Path, timesfm_path: Path,
                                  output_root: Path, *, built_at_utc: str | None = None) -> dict:
    """Append TimesFM features to the base event dataset after provenance checks.

    Both inputs must be immutable outputs with manifests. The join requires the
    TimesFM run to reference the same underlying event CSV as the base research
    dataset, and checks issuer/timestamp mappings for every event ID.
    """
    research_path, timesfm_path, output_root = map(Path, (research_path, timesfm_path, output_root))
    research_manifest_path = research_path.parent / "manifest.json"
    timesfm_manifest_path = timesfm_path.parent / "manifest.json"
    research_manifest = _manifest(research_manifest_path)
    timesfm_manifest = _manifest(timesfm_manifest_path)
    research_raw, research_columns, research_rows = _read_csv(research_path)
    timesfm_raw, timesfm_columns, timesfm_rows = _read_csv(timesfm_path)

    if research_manifest.get("schema_version") != "form4-event-research-dataset-manifest-v1":
        raise ValueError("Unsupported event research dataset manifest schema")
    if timesfm_manifest.get("schema_version") != "timesfm-event-features-manifest-v1":
        raise ValueError("Unsupported TimesFM feature manifest schema")
    _check_output(research_raw, research_rows, research_manifest, "Research dataset")
    _check_output(timesfm_raw, timesfm_rows, timesfm_manifest, "TimesFM feature")

    event_input = research_manifest.get("event_input", {})
    event_hash = event_input.get("sha256")
    if not event_hash or timesfm_manifest.get("input_events", {}).get("sha256") != event_hash:
        raise ValueError("TimesFM features and research dataset were built from different event CSVs")

    research_ids = _unique(research_rows, "Research dataset")
    timesfm_ids = _unique(timesfm_rows, "TimesFM feature")
    if research_ids != timesfm_ids:
        raise ValueError("TimesFM feature event IDs do not match the research dataset")
    required = {"issuer_cik", "issuer_symbol", "event_available_at_utc", "forecast_status"}
    if not required.issubset(timesfm_columns):
        raise ValueError(f"TimesFM CSV is missing required fields: {sorted(required - set(timesfm_columns))}")
    for field in ("issuer_cik", "issuer_symbol", "historical_available_after_utc"):
        if field not in research_columns:
            raise ValueError(f"Research dataset is missing required field: {field}")

    timesfm_by_id = {row["event_id"]: row for row in timesfm_rows}
    for event in research_rows:
        feature = timesfm_by_id[event["event_id"]]
        if feature["issuer_cik"] != event["issuer_cik"] or feature["issuer_symbol"] != event["issuer_symbol"]:
            raise ValueError(f"TimesFM issuer does not match event {event['event_id']}")
        expected_time = utc_text(parse_timestamp(event["historical_available_after_utc"]))
        observed_time = utc_text(parse_timestamp(feature["event_available_at_utc"]))
        if observed_time != expected_time:
            raise ValueError(f"TimesFM timestamp does not match event {event['event_id']}")

    feature_fields = [field for field in timesfm_columns if field != "event_id"]
    added_columns = [f"timesfm_{field}" for field in feature_fields]
    output_columns = research_columns + added_columns
    if len(output_columns) != len(set(output_columns)):
        raise ValueError("TimesFM feature names collide with research dataset columns")
    output_rows = []
    for event in research_rows:
        row = dict(event)
        feature = timesfm_by_id[event["event_id"]]
        row.update({f"timesfm_{field}": value for field, value in feature.items() if field != "event_id"})
        output_rows.append(row)

    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output_path = folder / "event_research_dataset_with_timesfm.csv"
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=output_columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output_rows)

    statuses = sorted({row["forecast_status"] for row in timesfm_rows})
    summary = {
        "schema_version": "form4-event-research-timesfm-summary-v1",
        "event_count": len(output_rows),
        "timesfm_feature_column_count": len(added_columns),
        "timesfm_forecast_status_counts": {status: sum(row["forecast_status"] == status for row in timesfm_rows)
                                            for status in statuses},
        "forecast_status": "TIMESFM_FEATURES_RESEARCH_ONLY_NO_VALIDATED_SIGNAL",
    }
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "form4-event-research-timesfm-manifest-v1",
        "built_at_utc": stamp,
        "research_dataset_input": {"path": str(research_path), "sha256": _sha256(research_raw),
                                   "manifest_sha256": _sha256(research_manifest_path.read_bytes())},
        "timesfm_features_input": {"path": str(timesfm_path), "sha256": _sha256(timesfm_raw),
                                    "manifest_sha256": _sha256(timesfm_manifest_path.read_bytes()),
                                    "model": timesfm_manifest.get("model"),
                                    "point_in_time_policy": timesfm_manifest.get("point_in_time_policy"),
                                    "research_only": timesfm_manifest.get("research_only")},
        "join_key": "event_id",
        "event_input_sha256": event_hash,
        "forecast_target_alignment": "TimesFM returns are relative to the last close before event availability; existing labels begin at the first close after event availability. Align forecast origins and target horizons before training.",
        "forecast_status": "TIMESFM_FEATURES_RESEARCH_ONLY_NO_VALIDATED_SIGNAL",
        "output": {"path": str(output_path), "sha256": _sha256(output_path.read_bytes()),
                   "row_count": len(output_rows)},
        "summary_sha256": _sha256(summary_path.read_bytes()),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "csv_path": output_path, "summary": summary, "manifest": manifest}
