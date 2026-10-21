"""Parse ownership XML without conflating transactions, holdings, or owners."""

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import re
from xml.etree import ElementTree as ET

from .ledger import normalize_cik
from .xml_safety import strip_external_ownership_doctype

CODE_CLASSES = {"P": "purchase", "S": "sale", "A": "award_or_grant",
                "M": "exercise_or_conversion", "F": "tax_or_exercise_payment",
                "G": "gift", "D": "transfer_to_issuer", "K": "equity_swap",
                "J": "other", "C": "conversion", "W": "will_or_descent",
                "Z": "voting_trust", "U": "tender", "E": "expiration",
                "H": "cancellation", "I": "discretionary", "L": "small_acquisition",
                "O": "out_of_money_exercise", "X": "in_money_exercise"}


def text(node, path):
    value = node.findtext(path)
    return value.strip() if value and value.strip() else None


def boolean(value):
    if value is None:
        return None
    if value.lower() in ("true", "1"):
        return True
    if value.lower() in ("false", "0"):
        return False
    raise ValueError(f"Invalid XML boolean: {value}")


def decimal_text(value):
    if value is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid ownership numeric value") from error
    if not number.is_finite() or number < 0:
        raise ValueError("Ownership amounts must be finite and nonnegative")
    return format(number, "f")


def parse_form4(raw: bytes, filing: dict) -> dict:
    """Filing context is required: XML transaction dates do not establish availability."""
    if len(raw) > 10_000_000:
        raise ValueError("Ownership XML exceeds the 10 MB parser limit")
    # Reject alternate encodings; strip only an external DTD without resolving it.
    decoded = raw.decode("utf-8-sig")
    decoded = strip_external_ownership_doctype(decoded)
    root = ET.fromstring(decoded)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    if root.tag != "ownershipDocument":
        raise ValueError("Expected raw ownershipDocument XML, not an HTML viewer")
    form = text(root, "documentType")
    if form not in ("4", "4/A") or filing.get("form") != form:
        raise ValueError("Form 4 document and ledger form must agree")
    cik = normalize_cik(text(root, "issuer/issuerCik") or "")
    if cik != normalize_cik(filing["issuer_cik"]):
        raise ValueError("Ownership XML issuer does not match ledger")
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", filing["accession_number"]):
        raise ValueError("Invalid ledger accession")
    owners = []
    for owner in root.findall("reportingOwner"):
        owners.append({"cik": normalize_cik(text(owner, "reportingOwnerId/rptOwnerCik") or ""),
                       "name": text(owner, "reportingOwnerId/rptOwnerName"),
                       "is_director": boolean(text(owner, "reportingOwnerRelationship/isDirector")),
                       "is_officer": boolean(text(owner, "reportingOwnerRelationship/isOfficer")),
                       "is_ten_percent_owner": boolean(text(owner, "reportingOwnerRelationship/isTenPercentOwner")),
                       "is_other": boolean(text(owner, "reportingOwnerRelationship/isOther")),
                       "officer_title": text(owner, "reportingOwnerRelationship/officerTitle"),
                       "other_text": text(owner, "reportingOwnerRelationship/otherText")})
    if not owners:
        raise ValueError("Ownership document has no reporting owners")
    footnotes = {}
    for note in root.findall("footnotes/footnote"):
        identifier = note.get("id")
        if not identifier or identifier in footnotes:
            raise ValueError("Missing or duplicate footnote ID")
        footnotes[identifier] = " ".join("".join(note.itertext()).split())
    context = {key: filing.get(key) for key in (
        "issuer_cik", "accession_number", "accepted_at_utc", "observed_at_utc", "filing_date",
        "historical_available_after_utc", "live_available_after_utc", "availability_status", "source_url")}
    context.update({"schema_version": "form4-normalized-v1", "form": form,
                    "is_amendment": form == "4/A", "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "issuer_symbol": text(root, "issuer/issuerTradingSymbol"),
                    "issuer_name": text(root, "issuer/issuerName"), "reporting_owners": owners,
                    "aff10b5one": boolean(text(root, "aff10b5One")),
                    "date_original_submitted": text(root, "dateOfOriginalSubmission")})
    transactions, holdings = [], []
    for derivative, table in ((False, "nonDerivativeTable"), (True, "derivativeTable")):
        prefix = "derivative" if derivative else "nonDerivative"
        for kind, destination in (("Transaction", transactions), ("Holding", holdings)):
            for index, node in enumerate(root.findall(f"{table}/{prefix}{kind}")):
                references = sorted({ref.get("id") for ref in node.iter("footnoteId") if ref.get("id")})
                if any(ref not in footnotes for ref in references):
                    raise ValueError("Unresolved transaction footnote")
                row = dict(context)
                row.update({"record_id": f"{filing['accession_number']}:{prefix}{kind}:{index}",
                            "is_derivative": derivative, "security_title": text(node, "securityTitle/value"),
                            "ownership_type": text(node, "ownershipNature/directOrIndirectOwnership/value"),
                            "nature_of_ownership": text(node, "ownershipNature/natureOfOwnership/value"),
                            "shares_owned_after": decimal_text(text(node, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
                            "footnote_ids": references, "footnotes": {ref: footnotes[ref] for ref in references}})
                if kind == "Transaction":
                    transaction_date = text(node, "transactionDate/value")
                    if not transaction_date:
                        raise ValueError("Missing transaction date")
                    date.fromisoformat(transaction_date)
                    code = text(node, "transactionCoding/transactionCode")
                    shares = decimal_text(text(node, "transactionAmounts/transactionShares/value"))
                    price = decimal_text(text(node, "transactionAmounts/transactionPricePerShare/value"))
                    direction = text(node, "transactionAmounts/transactionAcquiredDisposedCode/value")
                    flags = []
                    if not filing.get("historical_available_after_utc"):
                        flags.append("UNKNOWN_AVAILABILITY")
                    if code not in CODE_CLASSES:
                        flags.append("UNKNOWN_TRANSACTION_CODE")
                    if code == "P" and direction != "A" or code == "S" and direction != "D":
                        flags.append("CODE_DIRECTION_MISMATCH")
                    if shares is None or price is None:
                        flags.append("MISSING_AMOUNT_OR_PRICE")
                    if form == "4/A":
                        flags.append("AMENDMENT_REQUIRES_RECONCILIATION")
                    row.update({"transaction_date": transaction_date, "transaction_code": code,
                                "transaction_class": CODE_CLASSES.get(code, "unknown"),
                                "shares": shares, "price_per_share": price, "acquired_disposed": direction,
                                "reported_gross_value": format(Decimal(shares) * Decimal(price), "f") if shares is not None and price is not None else None,
                                "is_purchase_sale_candidate": code in ("P", "S") and not derivative and not flags,
                                "quality_flags": flags,
                                "exercise_price": decimal_text(text(node, "conversionOrExercisePrice/value")),
                                "underlying_title": text(node, "underlyingSecurity/underlyingSecurityTitle/value"),
                                "underlying_shares": decimal_text(text(node, "underlyingSecurity/underlyingSecurityShares/value")),
                                "exercise_date": text(node, "exerciseDate/value"),
                                "expiration_date": text(node, "expirationDate/value")})
                destination.append(row)
    return {"schema_version": "form4-document-v1", "filing": context,
            "transactions": transactions, "holdings": holdings,
            "all_footnotes": footnotes, "remarks": text(root, "remarks")}

