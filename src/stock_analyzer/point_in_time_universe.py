"""Point-in-time Form 4 joins across a universe of issuers."""

import csv
from datetime import datetime, timezone
import json
from pathlib import Path

from .financials import aligned_cash_flow_features, select_as_of
from .ledger import normalize_cik, parse_timestamp, utc_text
from .point_in_time import (
    FACT_COLUMNS,
    METRIC_ORDER,
    _feature_row,
    _latest_fact,
    _sha256,
)


def _load_financial_histories(financials_root: Path) -> dict[str, tuple[Path, bytes, dict, dict]]:
    """Load and verify the newest normalized financial run for each CIK."""
    histories: dict[str, tuple[Path, bytes, dict, dict]] = {}
    candidates = sorted(financials_root.rglob("normalized.json"), key=lambda path: str(path.parent))
    if not candidates:
        raise ValueError(f"No normalized financial histories found under {financials_root}")
    for path in candidates:
        manifest_path = path.parent / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = path.read_bytes()
        if manifest.get("normalized_sha256") != _sha256(raw):
            raise ValueError(f"Financial normalization hash does not match its manifest: {path}")
        normalized = json.loads(raw.decode("utf-8"))
        if normalized.get("schema_version") != "financial-normalization-v1":
            continue
        cik = normalize_cik(normalized["issuer_cik"])
        prior = histories.get(cik)
        # Run directories use sortable UTC timestamp prefixes. Keep the newest
        # run deterministically; never use filesystem modification times.
        if prior is None or str(path.parent) > str(prior[0].parent):
            histories[cik] = (path, raw, manifest, normalized)
    if not histories:
        raise ValueError(f"No manifest-verified financial histories found under {financials_root}")
    return histories


def join_form4_events_to_financial_universe(events_path: Path, financials_root: Path,
                                            output_root: Path, *,
                                            built_at_utc: str | None = None) -> dict:
    """Join every event only to the financial history for its issuer CIK.

    Financial histories are hash-checked, and the latest normalized run by its
    sortable UTC output-directory name is used for each issuer. Filing facts
    are still selected by historical availability as of each event.
    """
    events_path, financials_root, output_root = map(Path, (events_path, financials_root, output_root))
    event_manifest = json.loads((events_path.parent / "manifest.json").read_text(encoding="utf-8"))
    events_raw = events_path.read_bytes()
    if _sha256(events_raw) != event_manifest.get("output", {}).get("sha256"):
        raise ValueError("Event CSV hash does not match its manifest")
    events = list(csv.DictReader(events_raw.decode("utf-8-sig").splitlines()))
    if len(events) != event_manifest.get("output", {}).get("row_count"):
        raise ValueError("Event CSV row count does not match its manifest")
    event_ids = [event.get("event_id") for event in events]
    if not all(event_ids) or len(event_ids) != len(set(event_ids)):
        raise ValueError("Event IDs must be present and unique")

    histories = _load_financial_histories(financials_root)
    required_ciks = {normalize_cik(event["issuer_cik"]) for event in events}
    missing = sorted(required_ciks - set(histories))
    if missing:
        raise ValueError("Missing normalized financial histories for issuer CIK(s): " + ", ".join(missing))

    output_rows = []
    event_counts: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    for event in events:
        cik = normalize_cik(event["issuer_cik"])
        path, _, _, normalized = histories[cik]
        event_counts[cik] = event_counts.get(cik, 0) + 1
        issue_counts[cik] = len(normalized.get("issues", []))
        event_time = parse_timestamp(event["historical_available_after_utc"])
        selected = select_as_of(normalized.get("facts", []), event["historical_available_after_utc"], mode="historical")
        all_selected = [*selected, *aligned_cash_flow_features(selected)]
        for metric in METRIC_ORDER:
            fact = _latest_fact([row for row in all_selected if row["metric"] == metric], metric)
            output_rows.append(_feature_row(event, metric, fact, event_time))

    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output_path = folder / "event_financial_features.csv"
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FACT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(output_rows)

    summary = {
        "schema_version": "form4-point-in-time-universe-summary-v1",
        "event_count": len(events),
        "issuer_count": len(required_ciks),
        "event_counts_by_issuer_cik": dict(sorted(event_counts.items())),
        "metric_count": len(METRIC_ORDER),
        "event_metric_rows": len(output_rows),
        "selected_fact_rows": sum(row["selection_status"] == "SELECTED_AS_OF_EVENT" for row in output_rows),
        "missing_metric_rows": sum(row["selection_status"] != "SELECTED_AS_OF_EVENT" for row in output_rows),
        "financial_issues_by_issuer_cik": dict(sorted(issue_counts.items())),
        "label_status": "NOT_CREATED",
    }
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    financial_inputs = [
        {"issuer_cik": cik, "path": str(histories[cik][0]), "sha256": _sha256(histories[cik][1])}
        for cik in sorted(required_ciks)
    ]
    manifest = {
        "schema_version": "form4-point-in-time-universe-manifest-v1",
        "built_at_utc": stamp,
        "event_input": {"path": str(events_path), "sha256": _sha256(events_raw)},
        "financials_inputs": financial_inputs,
        "output": {"path": str(output_path), "sha256": _sha256(output_path.read_bytes()),
                   "row_count": len(output_rows)},
        "summary_sha256": _sha256(summary_path.read_bytes()),
        "availability_policy": "historical_available_after_utc inclusive cutoff; same issuer CIK only",
        "forecast_status": "NOT_IMPLEMENTED",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "csv_path": output_path, "summary": summary, "manifest": manifest}
