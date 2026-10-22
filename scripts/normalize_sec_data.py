"""Normalize archived financial facts or one Form 4 with ledger context."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from stock_analyzer.financials import normalize_companyfacts, select_as_of, aligned_cash_flow_features
from stock_analyzer.form4 import parse_form4
from stock_analyzer.ledger import parse_timestamp, utc_text
from stock_analyzer.sec_client import (
    SecClient, UnsupportedSourceFormat, extract_ownership_document,
    ownership_archive_url, ownership_submission_url,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("financials", "form4"):
        child = commands.add_parser(command)
        child.add_argument("--ledger", type=Path, required=True)
        child.add_argument("--output-root", type=Path, default=Path("data/normalized"))
        if command == "financials":
            child.add_argument("--companyfacts", type=Path, required=True)
            child.add_argument("--as-of", required=True, help="Timezone-aware timestamp")
            child.add_argument("--mode", choices=("historical", "live"), default="historical")
            child.add_argument("--facts-observed-at", help="Snapshot acquisition timestamp; required for live selection")
        else:
            child.add_argument("--accession", required=True)
            source = child.add_mutually_exclusive_group(required=True)
            source.add_argument("--xml", type=Path)
            source.add_argument("--download", action="store_true")
            child.add_argument("--xml-observed-at", help="Required for local XML: actual acquisition timestamp")
            child.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT", ""))
            child.add_argument("--cache-dir", type=Path, default=Path("data/raw/sec"))
            child.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    ledger_raw = args.ledger.read_bytes()
    ledger = [json.loads(line) for line in ledger_raw.decode().splitlines() if line.strip()]
    source = {}
    if args.command == "financials":
        raw = args.companyfacts.read_bytes()
        source = {"path": str(args.companyfacts), "sha256": hashlib.sha256(raw).hexdigest()}
        if args.mode == "live" and not args.facts_observed_at:
            parser.error("--facts-observed-at is required for live financial selection")
        result = normalize_companyfacts(json.loads(raw, parse_float=Decimal), ledger, snapshot_sha256=source["sha256"],
                                        snapshot_observed_at=args.facts_observed_at)
        selected = select_as_of(result["facts"], args.as_of, mode=args.mode)
        result.update({"as_of_utc": utc_text(parse_timestamp(args.as_of)), "mode": args.mode,
                       "selected_facts": selected, "derived_features": aligned_cash_flow_features(selected)})
    else:
        matches = [row for row in ledger if row["accession_number"] == args.accession]
        if len(matches) != 1 or matches[0]["form"] not in ("4", "4/A"):
            raise ValueError("Exactly one matching Form 4 ledger record is required")
        filing = dict(matches[0])
        if args.download:
            url = ownership_archive_url(filing["issuer_cik"], args.accession,
                                        filing["primary_document"])
            client = SecClient(args.user_agent, args.cache_dir)
            try:
                snapshot = client.fetch_xml(url, refresh=args.refresh)
                raw, observed = snapshot.payload, snapshot.observed_at_utc
                source = {"url": url, "path": str(snapshot.path), "sha256": snapshot.sha256}
                filing["source_url"] = url
            except UnsupportedSourceFormat:
                submission_url = ownership_submission_url(filing["issuer_cik"], args.accession)
                snapshot = client.fetch_text(submission_url, refresh=args.refresh)
                raw = extract_ownership_document(snapshot.payload)
                observed = snapshot.observed_at_utc
                source = {
                    "url": submission_url,
                    "path": str(snapshot.path),
                    "sha256": snapshot.sha256,
                    "document_url": url,
                    "document_sha256": hashlib.sha256(raw).hexdigest(),
                    "extraction": "ownershipDocument from complete-submission text",
                }
                filing["source_url"] = submission_url
        else:
            if not args.xml_observed_at:
                parser.error("--xml-observed-at is required with local --xml; download time cannot be inferred from file mtime")
            raw, observed = args.xml.read_bytes(), utc_text(parse_timestamp(args.xml_observed_at))
            source = {"path": str(args.xml), "sha256": hashlib.sha256(raw).hexdigest()}
        if filing.get("accepted_at_utc") and parse_timestamp(observed) < parse_timestamp(filing["accepted_at_utc"]):
            raise ValueError("XML acquisition cannot precede filing acceptance")
        prior_live = filing.get("live_available_after_utc")
        filing["observed_at_utc"] = observed
        filing["live_available_after_utc"] = utc_text(max(parse_timestamp(observed), parse_timestamp(prior_live))) if prior_live else None
        result = parse_form4(raw, filing)
    stamp = utc_text(datetime.now(timezone.utc)).replace(":", "-")
    out = args.output_root / args.command / f"{stamp}-{os.getpid()}"
    out.mkdir(parents=True, exist_ok=False)
    normalized = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    (out / "normalized.json").write_bytes(normalized)
    manifest = {"schema_version": "sec-normalization-run-v1", "command": args.command,
                "ledger_path": str(args.ledger), "ledger_sha256": hashlib.sha256(ledger_raw).hexdigest(),
                "source": source, "normalized_sha256": hashlib.sha256(normalized).hexdigest(),
                "forecast_status": "NOT_IMPLEMENTED"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Normalized {args.command} -> {out}")


if __name__ == "__main__":
    main()
