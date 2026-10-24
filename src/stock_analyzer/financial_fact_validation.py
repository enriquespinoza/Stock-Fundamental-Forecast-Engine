"""Automated consistency checks for sampled SEC Company Facts rows.

These checks verify the normalization against the archived Company Facts
snapshot and recorded normalized artifact. They do not certify that a fact is
shown in the filing's rendered statements; that remains a source review.
"""

import csv
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path


OUTPUT_FIELDS = (
    "automated_validation_status",
    "automated_validation_details",
    "companyfacts_match_count",
    "normalized_fact_match_count",
    "source_url_identity_status",
)


def _portable_path(value: str) -> Path:
    """Read stored Windows paths on Windows and POSIX paths in tests/tools."""
    value = value.strip()
    if os.name != "nt":
        value = value.replace("\\", "/")
    return Path(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal_equal(left: str, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _same_optional_text(sample_value: str, source_value) -> bool:
    source_text = "" if source_value is None else str(source_value)
    return (sample_value or "") == source_text


def _companyfacts_candidates(payload: dict, row: dict) -> list[dict]:
    try:
        values = payload["facts"]["us-gaap"][row["tag"]]["units"][row["unit"]]
    except (KeyError, TypeError):
        return []
    accession = row["accession_number"]
    matches = []
    for fact in values:
        if fact.get("accn") != accession:
            continue
        if fact.get("form") != row["form"] or fact.get("filed") != row["filing_date"]:
            continue
        if not _same_optional_text(row.get("period_start", ""), fact.get("start")):
            continue
        if not _same_optional_text(row.get("period_end", ""), fact.get("end")):
            continue
        if not _decimal_equal(row.get("value", ""), fact.get("val")):
            continue
        if row.get("fiscal_year", "") and not _same_optional_text(row["fiscal_year"], fact.get("fy")):
            continue
        if row.get("fiscal_period", "") and not _same_optional_text(row["fiscal_period"], fact.get("fp")):
            continue
        matches.append(fact)
    return matches


def _normalized_candidates(payload: dict, row: dict) -> list[dict]:
    matches = []
    for fact in payload.get("facts", []):
        if fact.get("accession_number") != row["accession_number"]:
            continue
        if fact.get("form") != row["form"] or fact.get("filed_date") != row["filing_date"]:
            continue
        fields = (
            ("metric", "metric"), ("tag", "tag"), ("unit", "unit"),
            ("period_start", "period_start"), ("period_end", "period_end"),
        )
        if any(not _same_optional_text(row.get(csv_key, ""), fact.get(json_key))
               for csv_key, json_key in fields):
            continue
        if not _decimal_equal(row.get("value", ""), fact.get("value")):
            continue
        if row.get("fiscal_year", "") and not _same_optional_text(row["fiscal_year"], fact.get("fiscal_year")):
            continue
        if row.get("fiscal_period", "") and not _same_optional_text(row["fiscal_period"], fact.get("fiscal_period")):
            continue
        matches.append(fact)
    return matches


def _url_identity_status(row: dict) -> str:
    """Check SEC archive URL host/path accession and issuer CIK fields."""
    from urllib.parse import urlparse
    import re

    parsed = urlparse(row.get("source_url", ""))
    match = re.search(r"/Archives/edgar/data/(\d+)/(\d+)/", parsed.path, re.IGNORECASE)
    if parsed.scheme != "https" or parsed.netloc.lower() != "www.sec.gov" or not match:
        return "INVALID_SEC_ARCHIVE_URL"
    url_cik, url_accession = match.groups()
    expected_accession = "".join(ch for ch in row.get("accession_number", "") if ch.isdigit())
    if url_cik.lstrip("0") != row.get("issuer_cik", "").lstrip("0"):
        return "ISSUER_CIK_MISMATCH"
    if url_accession != expected_accession:
        return "ACCESSION_MISMATCH"
    return "MATCH"


def _audit_row(row: dict, cache: dict[Path, object]) -> dict:
    details = []
    companyfacts_count = normalized_count = 0
    try:
        raw_path = _portable_path(row["companyfacts_snapshot_path"])
        normalized_path = _portable_path(row["normalized_financials_path"])
        for path, expected_key in (
            (raw_path, "companyfacts_snapshot_sha256"),
            (normalized_path, "normalized_financials_sha256"),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"Missing artifact: {path}")
            expected_hash = row.get(expected_key, "").strip()
            actual_hash = _sha256(path)
            if expected_hash and actual_hash != expected_hash:
                raise ValueError(f"Hash mismatch for {path}")
        if raw_path not in cache:
            cache[raw_path] = json.loads(raw_path.read_text(encoding="utf-8"))
        if normalized_path not in cache:
            cache[normalized_path] = json.loads(normalized_path.read_text(encoding="utf-8"))
        companyfacts_count = len(_companyfacts_candidates(cache[raw_path], row))
        normalized_count = len(_normalized_candidates(cache[normalized_path], row))
        if companyfacts_count == 0:
            details.append("No exact accession/tag/unit/value/period match in archived Company Facts")
        if normalized_count == 0:
            details.append("No exact match in normalized financial artifact")
        if companyfacts_count and normalized_count:
            status = "MATCHES_COMPANYFACTS_AND_NORMALIZED_OUTPUT"
            details.append("Exact fact values and period match both stored artifacts")
        else:
            status = "REVIEW_MISMATCH_OR_MISSING_FACT"
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        status = "REVIEW_ARTIFACT_PROBLEM"
        details.append(str(exc))

    url_status = _url_identity_status(row)
    if url_status != "MATCH":
        details.append(url_status)
        if status == "MATCHES_COMPANYFACTS_AND_NORMALIZED_OUTPUT":
            status = "REVIEW_SOURCE_URL_IDENTITY"
    return {
        **row,
        "automated_validation_status": status,
        "automated_validation_details": "; ".join(details),
        "companyfacts_match_count": companyfacts_count,
        "normalized_fact_match_count": normalized_count,
        "source_url_identity_status": url_status,
    }


def validate_financial_fact_sample(sample_csv: Path, output_csv: Path) -> dict:
    """Audit rows against their hash-pinned Company Facts and normalized files.

    The input CSV is preserved. The output receives separate automated status
    columns; this tool never writes MANUAL_REVIEW_STATUS or CONFIRMED.
    """
    sample_csv, output_csv = Path(sample_csv), Path(output_csv)
    with sample_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(("accession_number", "issuer_cik", "form", "metric", "tag", "value", "unit", "period_end", "source_url", "companyfacts_snapshot_path", "normalized_financials_path")).issubset(reader.fieldnames):
            raise ValueError("Financial fact sample is missing required columns")
        rows = list(reader)
        fields = list(reader.fieldnames)
    cache = {}
    audited = [_audit_row(row, cache) for row in rows]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields + [field for field in OUTPUT_FIELDS if field not in fields], lineterminator="\n")
        writer.writeheader()
        writer.writerows(audited)
    counts = {}
    for row in audited:
        key = row["automated_validation_status"]
        counts[key] = counts.get(key, 0) + 1
    return {
        "input_rows": len(rows),
        "unique_accessions": len({row["accession_number"] for row in rows}),
        "automated_status_counts": counts,
        "output_csv": str(output_csv),
        "manual_source_confirmation_required": True,
        "note": "Automated matches verify normalization against the archived Company Facts snapshot; they do not independently verify the filing's rendered statement or XBRL context.",
    }
