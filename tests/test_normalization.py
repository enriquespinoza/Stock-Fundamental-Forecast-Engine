from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from stock_analyzer.financials import normalize_companyfacts, select_as_of, aligned_cash_flow_features
from stock_analyzer.form4 import parse_form4
from stock_analyzer.ledger import build_ledger
from stock_analyzer.sec_client import (
    SecClient, Snapshot, UnsupportedSourceFormat, extract_ownership_document,
    ownership_archive_url, ownership_submission_url,
)


def filing(form="4", amendment=False):
    page = {"accessionNumber": ["0000000001-26-000002" if amendment else "0000000001-26-000001"],
            "form": [form], "filingDate": ["2026-10-12" if amendment else "2026-10-09"],
            "reportDate": ["2026-06-30"], "primaryDocument": ["xslF345X05/ownership.xml"],
            "acceptanceDateTime": ["2026-10-12T10:00:00Z" if amendment else "2026-10-09T20:30:00Z"]}
    return build_ledger("1", [page], observed_at="2026-10-12T12:00:00Z")[0]


def xml(code="P", price="12.50", form="4", direction="A"):
    return f'''<?xml version="1.0"?>
<ownershipDocument xmlns="urn:test:ownership">
<documentType>{form}</documentType><aff10b5One>1</aff10b5One>
<issuer><issuerCik>1</issuerCik><issuerName>Fixture Corp</issuerName><issuerTradingSymbol>FIX</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>2</rptOwnerCik><rptOwnerName>Owner A</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>
<reportingOwner><reportingOwnerId><rptOwnerCik>3</rptOwnerCik><rptOwnerName>Owner B</rptOwnerName></reportingOwnerId></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
<transactionDate><value>2026-10-07</value></transactionDate>
<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100.5</value></transactionShares>
<transactionPricePerShare>{'<value>'+price+'</value>' if price is not None else ''}<footnoteId id="F1"/></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>{direction}</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>200</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
</nonDerivativeTransaction><nonDerivativeHolding><securityTitle><value>Other Class</value></securityTitle></nonDerivativeHolding></nonDerivativeTable>
<footnotes><footnote id="F1">Weighted average price; see range.</footnote></footnotes>
</ownershipDocument>'''.encode()


def fact(value=100, tag="Revenues", accession="0000000001-26-000001", form="10-Q", start="2026-04-01", end="2026-06-30", unit="USD"):
    entry = {"val": value, "accn": accession, "form": form, "filed": "2026-10-12" if form.endswith("/A") else "2026-10-09", "end": end,
             "fy": 2026, "fp": "Q2"}
    if start is not None:
        entry["start"] = start
    return {"cik": 1, "facts": {"us-gaap": {tag: {"units": {unit: [entry]}}}}}


class OwnershipTests(unittest.TestCase):
    def test_namespace_joint_owners_footnotes_and_decimal_value(self):
        result = parse_form4(xml(), filing())
        self.assertEqual(len(result["transactions"]), 1)
        row = result["transactions"][0]
        self.assertEqual(len(row["reporting_owners"]), 2)
        self.assertEqual(row["reported_gross_value"], "1256.250")
        self.assertEqual(row["transaction_class"], "purchase")
        self.assertTrue(row["is_purchase_sale_candidate"])
        self.assertTrue(row["aff10b5one"])
        self.assertEqual(len(result["holdings"]), 1)
        self.assertIn("Weighted average", row["footnotes"]["F1"])
        self.assertEqual(row["transaction_date"], "2026-10-07")
        self.assertEqual(row["historical_available_after_utc"], "2026-10-09T20:31:00Z")

    def test_sale_and_non_market_codes(self):
        self.assertTrue(parse_form4(xml(code="S", direction="D"), filing())["transactions"][0]["is_purchase_sale_candidate"])
        for code in ("A", "M", "F", "G", "J"):
            self.assertFalse(parse_form4(xml(code=code), filing())["transactions"][0]["is_purchase_sale_candidate"])

    def test_missing_price_is_unknown_not_zero(self):
        row = parse_form4(xml(price=None), filing())["transactions"][0]
        self.assertIsNone(row["price_per_share"])
        self.assertIsNone(row["reported_gross_value"])
        self.assertFalse(row["is_purchase_sale_candidate"])

    def test_amendment_is_preserved_and_not_automatically_aggregated(self):
        row = parse_form4(xml(form="4/A"), filing("4/A", True))["transactions"][0]
        self.assertTrue(row["is_amendment"])
        self.assertIn("AMENDMENT_REQUIRES_RECONCILIATION", row["quality_flags"])
        self.assertFalse(row["is_purchase_sale_candidate"])

    def test_unknown_plan_flag_and_code_direction_mismatch(self):
        raw = xml(direction="D").replace(b"<aff10b5One>1</aff10b5One>", b"")
        row = parse_form4(raw, filing())["transactions"][0]
        self.assertIsNone(row["aff10b5one"])
        self.assertIn("CODE_DIRECTION_MISMATCH", row["quality_flags"])

    def test_derivatives_do_not_become_cash_stock_purchase_signals(self):
        raw = xml().replace(b"nonDerivative", b"derivative")
        row = parse_form4(raw, filing())["transactions"][0]
        self.assertTrue(row["is_derivative"])
        self.assertFalse(row["is_purchase_sale_candidate"])

    def test_entities_issuer_mismatch_and_bad_footnotes_rejected(self):
        for raw in (b'<!DOCTYPE ownershipDocument [<!ENTITY x "abc">]><ownershipDocument/>',
                    xml().replace(b"<issuerCik>1", b"<issuerCik>9"),
                    xml().replace(b'id="F1"/>', b'id="missing"/>')):
            with self.assertRaises(ValueError):
                parse_form4(raw, filing())

    def test_nonfinite_and_negative_amounts_rejected(self):
        for value in ("NaN", "Infinity", "-1"):
            with self.assertRaises(ValueError):
                parse_form4(xml(price=value), filing())

    def test_unknown_availability_and_code_are_not_candidates(self):
        context = filing()
        context["historical_available_after_utc"] = None
        row = parse_form4(xml(), context)["transactions"][0]
        self.assertFalse(row["is_purchase_sale_candidate"])
        self.assertIn("UNKNOWN_AVAILABILITY", row["quality_flags"])
        row = parse_form4(xml(code="?"), filing())["transactions"][0]
        self.assertIn("UNKNOWN_TRANSACTION_CODE", row["quality_flags"])

    def test_safe_viewer_path_and_archive_xml_cache(self):
        context = filing()
        self.assertIn("xslF345X05/ownership.xml", context["source_url"])
        self.assertEqual(ownership_archive_url("0000789019", "0000950170-24-020392",
                                               "xslF345X05/ownership.xml"),
                         "https://www.sec.gov/Archives/edgar/data/789019/000095017024020392/xslF345X05/ownership.xml")
        for path in ("../ownership.xml", "xsl/../../ownership.xml", "ownership.htm"):
            with self.assertRaises(ValueError):
                ownership_archive_url("0000789019", "0000950170-24-020392", path)
        self.assertEqual(ownership_submission_url("0001551152", "0001997505-26-000010"),
                         "https://www.sec.gov/Archives/edgar/data/1551152/000199750526000010/0001997505-26-000010.txt")
        for cik, accession in (("../1", "0000000001-24-000001"),
                               ("1", "../bad")):
            with self.assertRaises(ValueError):
                ownership_submission_url(cik, accession)
        embedded = b"SEC complete submission\n" + xml() + b"\nend"
        self.assertEqual(extract_ownership_document(embedded), xml())
        with self.assertRaises(ValueError):
            extract_ownership_document(b"<html>no ownership XML</html>")

        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def opener(request, timeout):
                calls.append(request)
                return BytesIO(xml())
            client = SecClient("Research test@example.com", Path(directory), opener=opener)
            url = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/xslF345X05/ownership.xml"
            first = client.fetch_xml(url)
            self.assertEqual(first.path.suffix, ".xml")
            self.assertTrue(client.fetch_xml(url).from_cache)
            self.assertEqual(len(calls), 1)
            text_url = ownership_submission_url("1", "0000000001-26-000001")
            with patch.object(client, "opener", side_effect=lambda request, timeout: calls.append(request) or BytesIO(b"submission")):
                first_text = client.fetch_text(text_url)
                self.assertEqual(first_text.payload, b"submission")
                self.assertTrue(client.fetch_text(text_url).from_cache)
            self.assertEqual(len(calls), 2)
            with self.assertRaises(ValueError):
                client.fetch_xml("https://www.sec.gov/Archives/../../bad.xml")


class FinancialTests(unittest.TestCase):
    def normalize(self, payload, ledger=None, **kwargs):
        return normalize_companyfacts(payload, ledger or [filing("10-Q")], snapshot_sha256="a" * 64, **kwargs)

    def test_quarter_ytd_and_annual_are_not_conflated(self):
        payload = fact()
        entries = payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        entries.append({**entries[0], "start": "2026-01-01", "val": 180})
        entries.append({**entries[0], "start": "2025-07-01", "val": 360})
        rows = self.normalize(payload)["facts"]
        self.assertEqual({row["period_kind"] for row in rows}, {"quarter", "ytd_half_year", "annual"})
        self.assertEqual(len(select_as_of(rows, "2026-10-10T00:00:00Z")), 3)

    def test_instant_and_shares_units(self):
        rows = self.normalize(fact(200, "CommonStockSharesOutstanding", start=None, unit="shares"))["facts"]
        self.assertEqual(rows[0]["period_kind"], "instant")
        self.assertEqual(rows[0]["value"], "200")

    def test_restatement_not_visible_before_acceptance(self):
        payload = fact()
        amended = fact(90, accession="0000000001-26-000002", form="10-Q/A")
        payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"].extend(amended["facts"]["us-gaap"]["Revenues"]["units"]["USD"])
        rows = self.normalize(payload, [filing("10-Q"), filing("10-Q/A", True)])["facts"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(select_as_of(rows, "2026-10-10T00:00:00Z")[0]["value"], "100")
        self.assertEqual(select_as_of(rows, "2026-10-12T10:01:00Z")[0]["value"], "90")

    def test_live_financials_require_actual_facts_observation(self):
        rows = self.normalize(fact())["facts"]
        self.assertEqual(select_as_of(rows, "2026-10-20T00:00:00Z", mode="live"), [])
        rows = self.normalize(fact(), snapshot_observed_at="2026-10-13T00:00:00Z")["facts"]
        self.assertEqual(select_as_of(rows, "2026-10-12T12:00:00Z", mode="live"), [])
        self.assertEqual(len(select_as_of(rows, "2026-10-13T00:00:00Z", mode="live")), 1)

    def test_unavailable_context_wrong_units_and_bad_values_are_reported(self):
        unknown = filing("10-Q")
        unknown["historical_available_after_utc"] = None
        cases = [(fact(unit="EUR"), None, "UNSUPPORTED_UNIT"),
                 (fact(), [unknown], "UNKNOWN_AVAILABILITY"),
                 (fact(accession="0000000001-26-999999"), None, "MISSING_FILING_CONTEXT"),
                 (fact(float("nan")), None, "INVALID_VALUE_OR_PERIOD"),
                 (fact(start="2026-07-01"), None, "INVALID_VALUE_OR_PERIOD")]
        for payload, ledger, reason in cases:
            result = self.normalize(payload, ledger)
            self.assertEqual(result["facts"], [])
            self.assertIn(reason, {issue["reason"] for issue in result["issues"]})

    def test_tag_precedence_and_duplicate_conflict(self):
        payload = fact()
        payload["facts"]["us-gaap"].update(fact(105, "RevenueFromContractWithCustomerExcludingAssessedTax")["facts"]["us-gaap"])
        selected = select_as_of(self.normalize(payload)["facts"], "2026-10-10T00:00:00Z")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["value"], "105")
        entries = payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        entries.append({**entries[0], "val": 999})
        with self.assertRaises(ValueError):
            self.normalize(payload)

    def test_cash_flow_requires_exact_period_and_version_alignment(self):
        payload = fact(120, "NetCashProvidedByUsedInOperatingActivities", start="2026-01-01")
        payload["facts"]["us-gaap"].update(fact(30, "PaymentsToAcquirePropertyPlantAndEquipment", start="2026-01-01")["facts"]["us-gaap"])
        selected = select_as_of(self.normalize(payload)["facts"], "2026-10-10T00:00:00Z")
        self.assertEqual(aligned_cash_flow_features(selected)[0]["value"], "90")
        changed = deepcopy(selected)
        changed[0]["period_start"] = "2026-04-01"
        self.assertEqual(aligned_cash_flow_features(changed), [])
        changed = deepcopy(selected)
        changed[0]["accession_number"] = "different-version"
        self.assertEqual(aligned_cash_flow_features(changed), [])

    def test_cli_financials_and_form4_create_hashed_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "ledger.jsonl"
            ledger_path.write_text(json.dumps(filing("10-Q")) + "\n" + json.dumps({**filing(), "accession_number": "0000000001-26-000003"}) + "\n")
            facts = root / "facts.json"
            facts.write_text(json.dumps(fact()))
            ownership = root / "ownership.xml"
            ownership.write_bytes(xml())
            script = Path(__file__).resolve().parents[1] / "scripts" / "normalize_sec_data.py"
            common = ["--ledger", str(ledger_path), "--output-root", str(root / "output")]
            for extra in (["financials", "--companyfacts", str(facts), "--as-of", "2026-10-10T00:00:00Z"],
                          ["form4", "--xml", str(ownership), "--accession", "0000000001-26-000003", "--xml-observed-at", "2026-10-13T00:00:00Z"]):
                with patch.object(sys, "argv", [str(script), *extra, *common]):
                    runpy.run_path(str(script), run_name="__main__")
            manifests = list((root / "output").glob("*/*/manifest.json"))
            self.assertEqual(len(manifests), 2)
            for manifest in manifests:
                self.assertEqual(json.loads(manifest.read_text())["forecast_status"], "NOT_IMPLEMENTED")
            result = json.loads(next((root / "output").glob("form4/*/normalized.json")).read_text())
            self.assertEqual(result["transactions"][0]["live_available_after_utc"], "2026-10-13T00:00:00Z")

    def test_form4_cli_falls_back_to_complete_submission_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "ledger.jsonl"
            row = {**filing(), "accession_number": "0000000001-26-000003"}
            ledger_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "normalize_sec_data.py"
            snapshot = Snapshot(
                payload=b"complete submission\n" + xml() + b"\n",
                source_url="https://www.sec.gov/Archives/edgar/data/1/000000000126000003/0000000001-26-000003.txt",
                observed_at_utc="2026-10-13T00:00:00Z",
                sha256=__import__("hashlib").sha256(b"complete submission\n" + xml() + b"\n").hexdigest(), path=root / "submission.txt", from_cache=False,
            )
            with patch.object(sys, "argv", [
                str(script), "form4", "--ledger", str(ledger_path), "--accession",
                row["accession_number"], "--download", "--user-agent",
                "Research test@example.com", "--output-root", str(root / "output"),
            ]), patch("stock_analyzer.sec_client.SecClient.fetch_xml",
                     side_effect=UnsupportedSourceFormat("viewer response")), \
                 patch("stock_analyzer.sec_client.SecClient.fetch_text", return_value=snapshot):
                runpy.run_path(str(script), run_name="__main__")
            manifest = json.loads(next((root / "output").glob("form4/*/manifest.json")).read_text())
            self.assertEqual(manifest["source"]["sha256"], snapshot.sha256)
            self.assertEqual(manifest["source"]["document_sha256"], __import__("hashlib").sha256(xml()).hexdigest())
            self.assertEqual(manifest["source"]["extraction"], "ownershipDocument from complete-submission text")
            normalized = json.loads(next((root / "output").glob("form4/*/normalized.json")).read_text())
            self.assertEqual(normalized["filing"]["source_url"], snapshot.source_url)


if __name__ == "__main__":
    unittest.main()
