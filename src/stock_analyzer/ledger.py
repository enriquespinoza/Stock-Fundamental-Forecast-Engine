"""Conservative, version-preserving filing availability ledger."""

from datetime import datetime, timedelta, timezone
import re

SCHEMA_VERSION = "filing-ledger-v1"


def normalize_cik(value: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,10}", value) or int(value) == 0:
        raise ValueError("CIK must contain 1–10 digits and be nonzero")
    return value.zfill(10)


def parse_timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include a timezone; availability cannot be guessed")
    return result.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Timezone required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def filing_records(payload: dict) -> list[dict]:
    """Accept both primary submissions and additional historical pages."""
    columns = payload.get("filings", {}).get("recent", payload)
    if "accessionNumber" not in columns:
        raise ValueError("Submissions page has no accessionNumber column")
    count = len(columns["accessionNumber"])
    required = ("accessionNumber", "form", "filingDate", "primaryDocument")
    for name in required:
        if name not in columns or not isinstance(columns[name], list) or len(columns[name]) != count:
            raise ValueError(f"Invalid submissions column: {name}")
    for name in ("acceptanceDateTime", "reportDate"):
        if name in columns and (not isinstance(columns[name], list) or len(columns[name]) != count):
            raise ValueError(f"Invalid submissions column: {name}")
    return [{key: values[i] for key, values in columns.items()
             if isinstance(values, list) and len(values) == count} for i in range(count)]


def build_ledger(cik: str, pages: list[dict], *, observed_at: str,
                 processing_delay_seconds: int = 60) -> list[dict]:
    cik = normalize_cik(cik)
    observed = parse_timestamp(observed_at)
    if processing_delay_seconds < 0:
        raise ValueError("Processing delay must be nonnegative")
    by_accession = {}
    for page in pages:
        if "cik" in page and normalize_cik(str(page["cik"])) != cik:
            raise ValueError("Submissions issuer does not match requested CIK")
        for record in filing_records(page):
            accession = record["accessionNumber"]
            if not isinstance(accession, str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
                raise ValueError("Invalid accession number")
            if accession in by_accession and by_accession[accession] != record:
                raise ValueError(f"Conflicting duplicate accession: {accession}")
            by_accession[accession] = record
    rows = []
    for accession, record in sorted(by_accession.items()):
        accepted = record.get("acceptanceDateTime")
        availability = None
        status = "UNKNOWN_ACCEPTANCE_TIME"
        if accepted:
            availability = parse_timestamp(accepted) + timedelta(seconds=processing_delay_seconds)
            if parse_timestamp(accepted) > observed:
                raise ValueError("Filing acceptance is later than snapshot observation")
            status = "TIMESTAMP_AVAILABLE"
        document = record["primaryDocument"]
        if not isinstance(document, str) or (document and (
            not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", document)
            or any(part in (".", "..") for part in document.split("/"))
        )):
            raise ValueError("Invalid primary document name")
        rows.append({
            "schema_version": SCHEMA_VERSION, "issuer_cik": cik,
            "accession_number": accession, "form": record["form"],
            "is_amendment": record["form"].endswith("/A"),
            "filing_date": record["filingDate"], "report_date": record.get("reportDate") or None,
            "accepted_at_utc": utc_text(parse_timestamp(accepted)) if accepted else None,
            "historical_available_after_utc": utc_text(availability) if availability else None,
            "live_available_after_utc": utc_text(max(observed, availability)) if availability else None,
            "observed_at_utc": utc_text(observed), "availability_status": status,
            "primary_document": document,
            "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}" if document else None,
        })
    return rows


def eligible_as_of(rows: list[dict], as_of: str, *, mode: str = "historical") -> list[dict]:
    """Timestamp filter only; trading-session mapping is a later explicit step."""
    if mode not in ("historical", "live"):
        raise ValueError("mode must be historical or live")
    cutoff = parse_timestamp(as_of)
    key = f"{mode}_available_after_utc"
    return [row for row in rows if row.get(key) and parse_timestamp(row[key]) <= cutoff]

