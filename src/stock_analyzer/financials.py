"""Normalize a narrow US-GAAP fact set; select only eligible filing versions."""

from datetime import date
from decimal import Decimal, InvalidOperation

from .ledger import normalize_cik, parse_timestamp, utc_text

# Ordered alternatives are selection preferences, not quantities to sum.
METRICS = {
    "revenue": ("duration", "USD", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")),
    "net_income": ("duration", "USD", ("NetIncomeLoss",)),
    "operating_income": ("duration", "USD", ("OperatingIncomeLoss",)),
    "operating_cash_flow": ("duration", "USD", ("NetCashProvidedByUsedInOperatingActivities",)),
    "capital_expenditures": ("duration", "USD", ("PaymentsToAcquirePropertyPlantAndEquipment",)),
    "assets": ("instant", "USD", ("Assets",)),
    "liabilities": ("instant", "USD", ("Liabilities",)),
    "equity": ("instant", "USD", ("StockholdersEquity",)),
    "cash": ("instant", "USD", ("CashAndCashEquivalentsAtCarryingValue",)),
    "shares_outstanding": ("instant", "shares", ("CommonStockSharesOutstanding",)),
}


def period_kind(start, end):
    if start is None:
        return "instant"
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if days <= 0:
        raise ValueError("Fact period ends before it starts")
    for lower, upper, label in ((70, 110, "quarter"), (150, 210, "ytd_half_year"),
                               (240, 300, "ytd_nine_months"), (330, 400, "annual")):
        if lower <= days <= upper:
            return label
    return "other_duration"


def normalize_companyfacts(payload: dict, ledger: list[dict], *, snapshot_sha256: str,
                           snapshot_observed_at: str | None = None) -> dict:
    cik = normalize_cik(str(payload["cik"]))
    filings = {}
    for row in ledger:
        if normalize_cik(row["issuer_cik"]) != cik:
            raise ValueError("Financial ledger issuer mismatch")
        accession = row["accession_number"]
        if accession in filings and filings[accession] != row:
            raise ValueError("Conflicting ledger accession")
        filings[accession] = row
    rows, issues = [], []
    gaap = payload.get("facts", {}).get("us-gaap", {})
    for metric, (kind, expected_unit, tags) in METRICS.items():
        found = False
        for rank, tag in enumerate(tags):
            for unit, entries in gaap.get(tag, {}).get("units", {}).items():
                for fact in entries:
                    found = True
                    accession = fact.get("accn")
                    filing = filings.get(accession)
                    reason = None
                    if unit != expected_unit:
                        reason = "UNSUPPORTED_UNIT"
                    elif not filing:
                        reason = "MISSING_FILING_CONTEXT"
                    elif fact.get("form") not in ("10-K", "10-K/A", "10-Q", "10-Q/A"):
                        reason = "UNSUPPORTED_FORM"
                    elif filing["form"] != fact.get("form") or filing["filing_date"] != fact.get("filed"):
                        reason = "FILING_CONTEXT_MISMATCH"
                    elif not filing.get("historical_available_after_utc"):
                        reason = "UNKNOWN_AVAILABILITY"
                    if reason:
                        issues.append({"metric": metric, "tag": tag, "accession": accession, "reason": reason})
                        continue
                    try:
                        end = fact["end"]
                        date.fromisoformat(end)
                        start = fact.get("start")
                        if kind == "duration" and not start or kind == "instant" and start is not None:
                            raise ValueError("Fact period type mismatch")
                        classification = period_kind(start, end)
                        if date.fromisoformat(end) > parse_timestamp(filing["accepted_at_utc"]).date():
                            raise ValueError("Financial period ends after filing acceptance")
                        value = fact["val"]
                        if isinstance(value, bool):
                            raise ValueError("Boolean financial value")
                        number = Decimal(str(value))
                        if not number.is_finite():
                            raise ValueError("Nonfinite financial value")
                    except (KeyError, TypeError, ValueError, InvalidOperation):
                        issues.append({"metric": metric, "tag": tag, "accession": accession, "reason": "INVALID_VALUE_OR_PERIOD"})
                        continue
                    row = {"schema_version": "financial-fact-v1", "issuer_cik": cik,
                           "metric": metric, "taxonomy": "us-gaap", "tag": tag, "tag_preference": rank,
                           "unit": unit, "value": format(number, "f"), "period_start": start,
                           "period_end": end, "period_kind": classification, "fiscal_year": fact.get("fy"),
                           "fiscal_period": fact.get("fp"), "frame": fact.get("frame"),
                           "accession_number": accession, "form": fact["form"], "filed_date": fact["filed"],
                           "snapshot_sha256": snapshot_sha256}
                    row.update({key: filing.get(key) for key in ("accepted_at_utc", "observed_at_utc",
                               "historical_available_after_utc", "live_available_after_utc", "source_url")})
                    # Live financial values cannot predate acquisition of the facts snapshot.
                    prior_live = row["live_available_after_utc"]
                    row["facts_observed_at_utc"] = utc_text(parse_timestamp(snapshot_observed_at)) if snapshot_observed_at else None
                    row["live_available_after_utc"] = utc_text(max(parse_timestamp(prior_live), parse_timestamp(snapshot_observed_at))) if prior_live and snapshot_observed_at else None
                    rows.append(row)
        if not found:
            issues.append({"metric": metric, "reason": "MISSING_METRIC"})
    # Company Facts can repeat identical facts with different frame metadata.
    unique = {}
    for row in rows:
        identity = (row["metric"], row["tag"], row["unit"], row["period_start"], row["period_end"], row["accession_number"])
        if identity in unique and unique[identity]["value"] != row["value"]:
            raise ValueError("Conflicting values within the same filing and fact period")
        if identity not in unique:
            unique[identity] = row
    return {"schema_version": "financial-normalization-v1", "issuer_cik": cik,
            "facts": sorted(unique.values(), key=lambda row: (row["metric"], row["period_end"], row["period_start"] or "", row["accession_number"], row["tag"])),
            "issues": issues}


def select_as_of(rows: list[dict], as_of: str, *, mode="historical") -> list[dict]:
    if mode not in ("historical", "live"):
        raise ValueError("mode must be historical or live")
    cutoff = parse_timestamp(as_of)
    groups = {}
    for row in rows:
        available = row.get(f"{mode}_available_after_utc")
        if not available or parse_timestamp(available) > cutoff:
            continue
        key = (row["issuer_cik"], row["metric"], row["unit"], row["period_start"], row["period_end"])
        groups.setdefault(key, []).append(row)
    selected = []
    for key in sorted(groups, key=str):
        candidates = groups[key]
        latest = max(parse_timestamp(row["accepted_at_utc"]) for row in candidates)
        candidates = [row for row in candidates if parse_timestamp(row["accepted_at_utc"]) == latest]
        preference = min(row["tag_preference"] for row in candidates)
        candidates = [row for row in candidates if row["tag_preference"] == preference]
        if len({row["value"] for row in candidates}) != 1:
            raise ValueError("Ambiguous simultaneous filing versions")
        selected.append(sorted(candidates, key=lambda row: row["accession_number"])[-1])
    return selected


def aligned_cash_flow_features(rows: list[dict]) -> list[dict]:
    """Derive FCF only from the same issuer, period, unit and filing version."""
    groups = {}
    for row in rows:
        if row["metric"] not in ("operating_cash_flow", "capital_expenditures"):
            continue
        key = (row["issuer_cik"], row["period_start"], row["period_end"], row["unit"], row["accession_number"])
        if row["metric"] in groups.setdefault(key, {}):
            raise ValueError("Cash flow derivation requires selected, unique facts")
        groups[key][row["metric"]] = row
    features = []
    for group in groups.values():
        if len(group) != 2:
            continue
        operating, capex = group["operating_cash_flow"], group["capital_expenditures"]
        if Decimal(capex["value"]) < 0:
            continue
        result = dict(operating)
        result.update({"schema_version": "financial-derived-v1", "metric": "free_cash_flow",
                       "value": format(Decimal(operating["value"]) - Decimal(capex["value"]), "f"),
                       "tag": None, "tag_preference": None,
                       "input_facts": [{"metric": row["metric"], "tag": row["tag"],
                                        "value": row["value"], "accession": row["accession_number"]}
                                       for row in (operating, capex)]})
        features.append(result)
    return features
