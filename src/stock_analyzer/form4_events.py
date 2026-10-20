"""Build a provenance-linked event table from normalized Form 4 documents."""

import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from .ledger import parse_timestamp, utc_text


EVENT_COLUMNS = (
    "event_id", "issuer_cik", "issuer_symbol", "accession_number", "form", "filing_date",
    "accepted_at_utc", "historical_available_after_utc", "observed_at_utc", "transaction_date",
    "transaction_code", "transaction_class", "security_title", "is_derivative", "acquired_disposed",
    "shares", "price_per_share", "reported_gross_value", "shares_owned_after", "ownership_type",
    "is_amendment", "aff10b5one", "reporting_owners_json", "quality_flags_json", "source_url", "raw_sha256",
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_candidate(transaction: dict, filing: dict) -> None:
    if transaction.get("transaction_code") not in ("P", "S"):
        raise ValueError("Candidate transaction must have P or S transaction code")
    if transaction.get("is_derivative") is not False:
        raise ValueError("Candidate transaction must be non-derivative")
    if filing.get("is_amendment") is True:
        raise ValueError("Amendment transactions require reconciliation before event export")
    if transaction.get("quality_flags"):
        raise ValueError("Candidate transaction has unresolved quality flags")
    for field in ("shares", "price_per_share", "historical_available_after_utc", "transaction_date"):
        if transaction.get(field) is None and field not in filing:
            raise ValueError(f"Candidate transaction is missing {field}")
    if transaction.get("shares") is None or transaction.get("price_per_share") is None:
        raise ValueError("Candidate transaction is missing shares or price")
    for field in ("shares", "price_per_share"):
        try:
            value = Decimal(transaction[field])
        except (InvalidOperation, TypeError) as error:
            raise ValueError(f"Candidate {field} is not numeric") from error
        if not value.is_finite() or value < 0:
            raise ValueError(f"Candidate {field} must be nonnegative and finite")
    available = transaction.get("historical_available_after_utc")
    if not available:
        available = filing.get("historical_available_after_utc")
    if not available:
        raise ValueError("Candidate transaction has no historical availability timestamp")
    parse_timestamp(available)


def _event_row(transaction: dict, filing: dict) -> dict:
    _validate_candidate(transaction, filing)
    available = transaction.get("historical_available_after_utc") or filing["historical_available_after_utc"]
    owner_fields = ("cik", "name", "is_director", "is_officer", "is_ten_percent_owner", "is_other", "officer_title")
    owners = [{key: owner.get(key) for key in owner_fields}
              for owner in filing.get("reporting_owners", [])]
    return {
        "event_id": transaction["record_id"],
        "issuer_cik": filing.get("issuer_cik"),
        "issuer_symbol": filing.get("issuer_symbol"),
        "accession_number": filing.get("accession_number"),
        "form": filing.get("form"),
        "filing_date": filing.get("filing_date"),
        "accepted_at_utc": filing.get("accepted_at_utc"),
        # This is the historical knowledge time used for as-of joins. Transaction
        # date remains separate and must never be used as the public-availability time.
        "historical_available_after_utc": utc_text(parse_timestamp(available)),
        "observed_at_utc": filing.get("observed_at_utc"),
        "transaction_date": transaction.get("transaction_date"),
        "transaction_code": transaction.get("transaction_code"),
        "transaction_class": transaction.get("transaction_class"),
        "security_title": transaction.get("security_title"),
        "is_derivative": transaction.get("is_derivative"),
        "acquired_disposed": transaction.get("acquired_disposed"),
        "shares": transaction.get("shares"),
        "price_per_share": transaction.get("price_per_share"),
        "reported_gross_value": transaction.get("reported_gross_value"),
        "shares_owned_after": transaction.get("shares_owned_after"),
        "ownership_type": transaction.get("ownership_type"),
        "is_amendment": filing.get("is_amendment"),
        "aff10b5one": filing.get("aff10b5one"),
        "reporting_owners_json": json.dumps(owners, sort_keys=True, separators=(",", ":")),
        "quality_flags_json": json.dumps(transaction.get("quality_flags", []), separators=(",", ":")),
        "source_url": filing.get("source_url"),
        "raw_sha256": filing.get("raw_sha256"),
    }


def build_form4_event_dataset(normalized_root: Path, output_root: Path, *,
                              built_at_utc: str | None = None) -> dict:
    """Write one CSV row per structurally eligible non-derivative P/S transaction.

    Multiple normalized runs for an accession are deduplicated by selecting the
    lexically latest immutable run directory. Events preserve their historical
    availability timestamp and do not calculate labels or forecast returns.
    """
    normalized_root, output_root = Path(normalized_root), Path(output_root)
    selected = {}
    artifact_count = 0
    for manifest_path in sorted(normalized_root.glob("form4/*/manifest.json")):
        manifest = _json(manifest_path)
        if manifest.get("command") != "form4":
            continue
        normalized_path = manifest_path.parent / "normalized.json"
        if not normalized_path.is_file():
            raise ValueError(f"Missing normalized output for {manifest_path}")
        raw = normalized_path.read_bytes()
        if _sha256(raw) != manifest.get("normalized_sha256"):
            raise ValueError(f"Normalized output hash mismatch: {normalized_path}")
        normalized = json.loads(raw.decode("utf-8"))
        filing = normalized.get("filing", {})
        accession = filing.get("accession_number")
        if not accession:
            raise ValueError(f"Missing accession in {normalized_path}")
        artifact_count += 1
        selected[accession] = (manifest_path.parent.name, normalized_path, manifest_path, manifest, normalized)

    events = []
    excluded = {}
    transaction_count = 0
    holdings_count = 0
    for accession, (_, normalized_path, manifest_path, manifest, normalized) in sorted(selected.items()):
        filing = normalized.get("filing", {})
        if manifest.get("source", {}).get("sha256") != filing.get("raw_sha256"):
            raise ValueError(f"Raw source hash mismatch for {accession}")
        holdings_count += len(normalized.get("holdings", []))
        for transaction in normalized.get("transactions", []):
            transaction_count += 1
            code = transaction.get("transaction_code") or "UNKNOWN"
            if transaction.get("is_purchase_sale_candidate") is True:
                events.append(_event_row(transaction, filing))
            else:
                flags = transaction.get("quality_flags", [])
                reason = "|".join(flags) if flags else (
                    "NON_DERIVATIVE_P_OR_S_FILTER" if code in ("P", "S") else "OTHER_TRANSACTION_CODE")
                excluded[reason] = excluded.get(reason, 0) + 1

    event_ids = [row["event_id"] for row in events]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("Duplicate candidate event IDs across normalized accessions")
    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    folder = output_root / stamp.replace(":", "-")
    folder.mkdir(parents=True, exist_ok=False)
    csv_path = folder / "candidate_transactions.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EVENT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(events)
    csv_hash = _sha256(csv_path.read_bytes())
    summary = {
        "schema_version": "form4-event-dataset-summary-v1",
        "unique_normalized_accessions": len(selected),
        "normalized_artifact_count": artifact_count,
        "duplicate_artifact_count": artifact_count - len(selected),
        "transaction_count": transaction_count,
        "holding_count": holdings_count,
        "candidate_event_count": len(events),
        "candidate_buy_count": sum(row["transaction_code"] == "P" for row in events),
        "candidate_sale_count": sum(row["transaction_code"] == "S" for row in events),
        "excluded_transaction_counts": dict(sorted(excluded.items())),
        "availability_timestamp_field": "historical_available_after_utc",
        "return_labels": "NOT_CREATED",
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "form4-event-dataset-manifest-v1",
        "built_at_utc": stamp,
        "normalized_root": str(normalized_root),
        "source_normalized_sha256": [
            {"accession_number": accession, "normalized_sha256": selected[accession][3].get("normalized_sha256"),
             "manifest_sha256": _sha256(selected[accession][2].read_bytes())}
            for accession in sorted(selected)
        ],
        "output": {"path": str(csv_path), "sha256": csv_hash, "row_count": len(events)},
        "summary_sha256": _sha256((folder / "summary.json").read_bytes()),
        "forecast_status": "NOT_IMPLEMENTED",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": folder, "csv_path": csv_path, "summary": summary, "manifest": manifest}
