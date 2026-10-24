"""Conservative item indexing for archived 8-K and 8-K/A primary documents."""

from html.parser import HTMLParser
import hashlib
import re

from .ledger import normalize_cik, parse_timestamp, utc_text

MAX_DOCUMENT_BYTES = 10_000_000
ITEM_RE = re.compile(r"\bitem\s+([1-9]\.\d{2})\b", re.IGNORECASE)

# Broad families are deliberately descriptive.  They are not conclusions about
# materiality or the direction of a stock-price response.
ITEM_FAMILIES = {
    "1.01": "material_agreement", "1.02": "agreement_termination",
    "1.03": "bankruptcy", "1.04": "mine_safety", "1.05": "cybersecurity",
    "2.01": "acquisition_or_disposition", "2.02": "earnings_or_financial_update",
    "2.03": "financial_obligation", "2.04": "triggering_event",
    "2.05": "restructuring_or_exit_cost", "2.06": "impairment",
    "3.01": "listing_status", "3.02": "unregistered_securities",
    "3.03": "securityholder_rights", "4.01": "auditor_change",
    "4.02": "nonreliance_or_restatement", "5.01": "control_change",
    "5.02": "director_or_officer_change", "5.03": "governance_change",
    "5.04": "compensation_arrangement", "5.05": "ethics_disclosure",
    "5.06": "shell_company_status", "5.07": "shareholder_vote",
    "5.08": "director_nominations", "6.01": "asset_backed_securities",
    "6.02": "asset_servicing", "6.03": "asset_backed_trustee_change",
    "6.04": "asset_backed_distribution", "6.05": "asset_backed_other",
    "7.01": "reg_fd_disclosure", "8.01": "other_event",
    "9.01": "exhibits",
}


class _VisibleText(HTMLParser):
    """Collect visible text while skipping script/style content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self._hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self._hidden:
            self._hidden -= 1

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def _text(raw: bytes) -> str:
    if not isinstance(raw, bytes):
        raise TypeError("8-K source must be bytes")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("8-K source exceeds the 10 MB parser limit")
    decoded = raw.decode("utf-8-sig", errors="replace")
    parser = _VisibleText()
    parser.feed(decoded)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def parse_8k(raw: bytes, filing: dict, *, source_observed_at: str) -> dict:
    """Index item references without inferring event meaning from filing date.

    The caller supplies one ledger row and the actual acquisition timestamp for
    these bytes. Unknown historical availability remains explicitly ineligible.
    """
    form = filing.get("form")
    if form not in ("8-K", "8-K/A"):
        raise ValueError("Ledger record must be form 8-K or 8-K/A")
    accession = filing.get("accession_number")
    if not isinstance(accession, str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        raise ValueError("Invalid ledger accession")
    issuer_cik = normalize_cik(filing.get("issuer_cik", ""))
    observed = utc_text(parse_timestamp(source_observed_at))
    accepted = filing.get("accepted_at_utc")
    if accepted and parse_timestamp(observed) < parse_timestamp(accepted):
        raise ValueError("Source acquisition cannot precede filing acceptance")

    text = _text(raw)
    # HTML SEC viewer pages are not filing documents. Require evidence that the
    # decoded body is an 8-K, while accepting both raw HTML and submission text.
    form_marker = re.search(r"\bFORM\s+(8-K(?:/A)?)\b", text, re.IGNORECASE)
    current_report_marker = re.search(r"\b8-K\s+CURRENT\s+REPORT\b", text, re.IGNORECASE)
    if not form_marker and not current_report_marker:
        raise ValueError("Source does not identify itself as an 8-K filing document")
    document_form = form_marker.group(1).upper() if form_marker else "8-K"
    if document_form != form:
        raise ValueError("8-K document form does not match ledger form")

    counts = {}
    for match in ITEM_RE.finditer(text):
        code = match.group(1)
        counts[code] = counts.get(code, 0) + 1
    items = [
        {"item": code, "family": ITEM_FAMILIES.get(code, "unclassified_item"),
         "mention_count": counts[code]}
        for code in sorted(counts, key=lambda value: (int(value.split(".")[0]), int(value.split(".")[1])))
    ]
    historical_available = filing.get("historical_available_after_utc")
    if historical_available:
        historical_available = utc_text(parse_timestamp(historical_available))
    live_available = filing.get("live_available_after_utc")
    if live_available:
        live_available = utc_text(max(parse_timestamp(live_available), parse_timestamp(observed)))
    else:
        live_available = observed

    return {
        "schema_version": "8k-item-index-v1",
        "issuer_cik": issuer_cik,
        "accession_number": accession,
        "form": form,
        "filing_date": filing.get("filing_date"),
        "accepted_at_utc": filing.get("accepted_at_utc"),
        "historical_available_after_utc": historical_available,
        "observed_at_utc": observed,
        "live_available_after_utc": live_available,
        "availability_status": filing.get("availability_status"),
        "is_amendment": form == "8-K/A",
        "source_url": filing.get("source_url"),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "historical_research_eligible": bool(
            historical_available and filing.get("availability_status") == "TIMESTAMP_AVAILABLE"
        ),
        "item_count": len(items),
        "items": items,
    }
