"""Download or index one 8-K document and create an auditable item index."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from stock_analyzer.eight_k import parse_8k
from stock_analyzer.ledger import parse_timestamp, utc_text
from stock_analyzer.sec_client import SecClient, sec_archive_document_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    filing_selector = parser.add_mutually_exclusive_group(required=True)
    filing_selector.add_argument("--accession", help="Accession number to index")
    filing_selector.add_argument("--latest", action="store_true",
                                 help="Select the newest 8-K or 8-K/A in the ledger")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--document", type=Path,
                        help="Use locally archived primary document bytes")
    source.add_argument("--download", action="store_true",
                        help="Download and archive the ledger's SEC primary document")
    parser.add_argument("--observed-at",
                        help="Required with local --document; actual acquisition time")
    parser.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT", ""))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/raw/sec"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path("data/normalized"))
    args = parser.parse_args()

    ledger_bytes = args.ledger.read_bytes()
    ledger = [json.loads(line) for line in ledger_bytes.decode("utf-8").splitlines() if line.strip()]
    if args.latest:
        eligible = [row for row in ledger if row.get("form") in ("8-K", "8-K/A")]
        eligible.sort(key=lambda row: (row.get("filing_date") or "",
                                       row.get("accepted_at_utc") or "",
                                       row.get("accession_number") or ""))
        matches = eligible[-1:]
    else:
        matches = [row for row in ledger if row.get("accession_number") == args.accession]
    if len(matches) != 1:
        raise ValueError("Exactly one matching 8-K ledger record is required")
    filing = dict(matches[0])
    accession = filing["accession_number"]
    if args.download:
        url = sec_archive_document_url(filing["issuer_cik"], accession,
                                       filing.get("primary_document", ""))
        snapshot = SecClient(args.user_agent, args.cache_dir).fetch_document(url, refresh=args.refresh)
        raw, observed = snapshot.payload, snapshot.observed_at_utc
        source_meta = {"url": url, "path": str(snapshot.path), "sha256": snapshot.sha256,
                       "observed_at_utc": observed, "from_cache": snapshot.from_cache}
        filing["source_url"] = url
    else:
        if not args.observed_at:
            parser.error("--observed-at is required with local --document")
        raw = args.document.read_bytes()
        observed = utc_text(parse_timestamp(args.observed_at))
        source_meta = {"path": str(args.document), "sha256": hashlib.sha256(raw).hexdigest(),
                       "observed_at_utc": observed, "from_cache": False}
    result = parse_8k(raw, filing, source_observed_at=observed)
    print(f"Accession: {accession} ({filing['form']})")

    stamp = utc_text(datetime.now(timezone.utc)).replace(":", "-")
    out = args.output_root / "8k" / f"{stamp}-{os.getpid()}"
    out.mkdir(parents=True, exist_ok=False)
    normalized = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (out / "normalized.json").write_bytes(normalized)
    manifest = {
        "schema_version": "8k-item-index-run-v1",
        "command": "index_8k_filing",
        "ledger_path": str(args.ledger),
        "ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        "source": source_meta,
        "output": {"path": str(out / "normalized.json"),
                   "sha256": hashlib.sha256(normalized).hexdigest()},
        "research_status": "INDEX_ONLY_NO_FORECAST",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"8-K item index -> {out}")
    print(f"Items: {result['item_count']}; historical eligible: {result['historical_research_eligible']}")


if __name__ == "__main__":
    main()
