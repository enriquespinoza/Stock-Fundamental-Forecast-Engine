"""Point-in-time joins between Form 4 events and normalized financial facts."""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .financials import METRICS, aligned_cash_flow_features, select_as_of
from .ledger import normalize_cik, parse_timestamp, utc_text


FACT_COLUMNS = (
    "event_id", "issuer_cik", "event_available_at_utc", "metric", "value", "unit",
    "period_kind", "period_start", "period_end", "fact_accession_number", "fact_form",
    "tag", "tag_preference", "fact_available_after_utc", "fact_age_days", "selection_status",
)

METRIC_KINDS = {metric: kind for metric, (kind, _, _) in METRICS.items()}
METRIC_KINDS["free_cash_flow"] = "duration"
METRIC_ORDER = [*METRICS, "free_cash_flow"]
PERIOD_TIEBREAK = {"quarter": 0, "annual": 1, "ytd_nine_months": 2,
                   "ytd_half_year": 3, "other_duration": 4, "instant": 0}
REPORT_FAMILIES = {
    "10k": {"forms": {"10-K", "10-K/A"}, "duration_kind": "annual"},
    "10q": {"forms": {"10-Q", "10-Q/A"}, "duration_kind": "quarter"},
}
REPORT_METRIC_ORDER = [f"{metric}_{family}" for family in REPORT_FAMILIES
                       for metric in METRIC_ORDER]
PIT_METRIC_ORDER = [*METRIC_ORDER, *REPORT_METRIC_ORDER]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _latest_fact(rows: list[dict], metric: str) -> dict | None:
    if not rows:
        return None
    latest_end = max(row["period_end"] for row in rows)
    candidates = [row for row in rows if row["period_end"] == latest_end]
    candidates.sort(key=lambda row: (
        PERIOD_TIEBREAK.get(row["period_kind"], 99),
        row.get("period_start") or "", row.get("accession_number", "")))
    best_rank = PERIOD_TIEBREAK.get(candidates[0]["period_kind"], 99)
    best = [row for row in candidates if PERIOD_TIEBREAK.get(row["period_kind"], 99) == best_rank]
    identities = {(row.get("period_start"), row.get("period_end"), row.get("value"), row.get("unit"))
                  for row in best}
    if len(identities) > 1:
        raise ValueError(f"Ambiguous latest {metric} fact for period ending {latest_end}")
    return sorted(best, key=lambda row: row.get("accession_number", ""))[-1]


def _latest_report_fact(rows: list[dict], metric: str, family: str) -> dict | None:
    """Select the latest annual 10-K or quarter-only 10-Q fact independently."""
    definition = REPORT_FAMILIES[family]
    eligible = [row for row in rows if row.get("form") in definition["forms"]
                and (row.get("period_kind") == "instant"
                     or row.get("period_kind") == definition["duration_kind"])]
    return _latest_fact(eligible, metric)


def _report_family_facts(facts: list[dict], as_of: str, family: str) -> list[dict]:
    """Keep version selection within the original 10-K or 10-Q filing family."""
    forms = REPORT_FAMILIES[family]["forms"]
    versions = [row for row in facts if row.get("form") in forms]
    selected = select_as_of(versions, as_of, mode="historical")
    return [*selected, *aligned_cash_flow_features(selected)]


def _feature_row(event: dict, metric: str, fact: dict | None, event_time) -> dict:
    base = {"event_id": event["event_id"], "issuer_cik": event["issuer_cik"],
            "event_available_at_utc": event["historical_available_after_utc"], "metric": metric}
    if fact is None:
        return {**base, "value": None, "unit": None, "period_kind": None, "period_start": None,
                "period_end": None, "fact_accession_number": None, "fact_form": None, "tag": None,
                "tag_preference": None, "fact_available_after_utc": None, "fact_age_days": None,
                "selection_status": "NO_ELIGIBLE_FACT_AS_OF_EVENT"}
    available = parse_timestamp(fact["historical_available_after_utc"])
    if available > event_time:
        raise ValueError("Point-in-time join selected a future financial fact")
    age_days = (event_time - available).total_seconds() / 86400
    return {**base, "value": fact["value"], "unit": fact["unit"], "period_kind": fact["period_kind"],
            "period_start": fact.get("period_start"), "period_end": fact["period_end"],
            "fact_accession_number": fact["accession_number"], "fact_form": fact["form"],
            "tag": fact["tag"], "tag_preference": fact.get("tag_preference"),
            "fact_available_after_utc": utc_text(available), "fact_age_days": f"{age_days:.6f}",
            "selection_status": "SELECTED_AS_OF_EVENT"}


def join_form4_events_to_financials(events_path: Path, financials_path: Path,
                                    output_root: Path, *, built_at_utc: str | None = None) -> dict:
    """For each event and metric, attach the latest eligible statement fact.

    Historical financial availability, never transaction date, drives selection.
    Duration facts ending on the same date prefer quarterly, then annual, then
    year-to-date forms. Period metadata remains attached so values are not
    silently treated as comparable quarters.
    """
    events_path, financials_path, output_root = Path(events_path), Path(financials_path), Path(output_root)
    event_manifest_path = events_path.parent / "manifest.json"
    financial_manifest_path = financials_path.parent / "manifest.json"
    event_manifest = json.loads(event_manifest_path.read_text(encoding="utf-8"))
    financial_manifest = json.loads(financial_manifest_path.read_text(encoding="utf-8"))
    events_raw, financials_raw = events_path.read_bytes(), financials_path.read_bytes()
    if _sha256(events_raw) != event_manifest.get("output", {}).get("sha256"):
        raise ValueError("Event CSV hash does not match its manifest")
    if _sha256(financials_raw) != financial_manifest.get("normalized_sha256"):
        raise ValueError("Financial normalization hash does not match its manifest")
    normalized_financials = json.loads(financials_raw.decode("utf-8"))
    if normalized_financials.get("schema_version") != "financial-normalization-v1":
        raise ValueError("Expected normalized financial fact history")
    facts = normalized_financials.get("facts", [])
    events = list(csv.DictReader(events_raw.decode("utf-8").splitlines()))
    if len(events) != event_manifest.get("output", {}).get("row_count"):
        raise ValueError("Event CSV row count does not match its manifest")
    event_ids = [event.get("event_id") for event in events]
    if not all(event_ids) or len(event_ids) != len(set(event_ids)):
        raise ValueError("Event IDs must be present and unique")

    financial_cik = normalize_cik(normalized_financials["issuer_cik"])
    output_rows = []
    for event in events:
        if normalize_cik(event["issuer_cik"]) != financial_cik:
            raise ValueError("Event issuer does not match financial normalization")
        event_time = parse_timestamp(event["historical_available_after_utc"])
        selected = select_as_of(facts, event["historical_available_after_utc"], mode="historical")
        derived = aligned_cash_flow_features(selected)
        all_selected = [*selected, *derived]
        report_selected = {family: _report_family_facts(
            facts, event["historical_available_after_utc"], family) for family in REPORT_FAMILIES}
        for metric in METRIC_ORDER:
            fact = _latest_fact([row for row in all_selected if row["metric"] == metric], metric)
            output_rows.append(_feature_row(event, metric, fact, event_time))
        for family in REPORT_FAMILIES:
            family_rows = report_selected[family]
            for metric in METRIC_ORDER:
                fact = _latest_report_fact(
                    [row for row in family_rows if row["metric"] == metric], metric, family)
                output_rows.append(_feature_row(event, f"{metric}_{family}", fact, event_time))

    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    output_path = folder / "event_financial_features.csv"
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FACT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(output_rows)
    summary = {
        "schema_version": "form4-point-in-time-join-summary-v2",
        "event_count": len(events), "metric_count": len(PIT_METRIC_ORDER),
        "base_metric_count": len(METRIC_ORDER), "report_family_metric_count": len(REPORT_METRIC_ORDER),
        "report_family_policy": "10-K duration facts annual only; 10-Q duration facts quarter only; instant facts retained by form",
        "event_metric_rows": len(output_rows),
        "selected_fact_rows": sum(row["selection_status"] == "SELECTED_AS_OF_EVENT" for row in output_rows),
        "missing_metric_rows": sum(row["selection_status"] != "SELECTED_AS_OF_EVENT" for row in output_rows),
        "selected_base_fact_rows": sum(row["metric"] in METRIC_ORDER and row["selection_status"] == "SELECTED_AS_OF_EVENT" for row in output_rows),
        "selected_10k_fact_rows": sum(row["metric"].endswith("_10k") and row["selection_status"] == "SELECTED_AS_OF_EVENT" for row in output_rows),
        "selected_10q_fact_rows": sum(row["metric"].endswith("_10q") and row["selection_status"] == "SELECTED_AS_OF_EVENT" for row in output_rows),
        "financials_with_issues": len(normalized_financials.get("issues", [])),
        "label_status": "NOT_CREATED",
    }
    summary_path = folder / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "form4-point-in-time-join-manifest-v2", "built_at_utc": stamp,
        "event_input": {"path": str(events_path), "sha256": _sha256(events_raw)},
        "financials_input": {"path": str(financials_path), "sha256": _sha256(financials_raw)},
        "output": {"path": str(output_path), "sha256": _sha256(output_path.read_bytes()),
                   "row_count": len(output_rows)},
        "summary_sha256": _sha256(summary_path.read_bytes()),
        "availability_policy": "historical_available_after_utc inclusive cutoff; each report family versioned separately",
        "forecast_status": "NOT_IMPLEMENTED",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "csv_path": output_path, "summary": summary, "manifest": manifest}
