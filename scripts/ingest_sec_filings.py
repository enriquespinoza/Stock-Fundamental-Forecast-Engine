"""Fetch submissions history and Company Facts; write a provenance-linked ledger."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from stock_analyzer.ledger import build_ledger, normalize_cik, parse_timestamp, utc_text
from stock_analyzer.sec_client import SecClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cik", required=True, type=normalize_cik)
    parser.add_argument("--output-root", type=Path, default=Path("data"))
    parser.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT", ""))
    parser.add_argument("--refresh", action="store_true", help="Fetch fresh snapshots instead of reusing cache")
    parser.add_argument("--recent-only", action="store_true", help="Explicitly omit additional historical submissions pages")
    parser.add_argument("--processing-delay-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.processing_delay_seconds < 0:
        parser.error("processing delay must be nonnegative")
    client = SecClient(args.user_agent, args.output_root / "raw" / "sec")
    snapshots = [client.fetch_json(f"https://data.sec.gov/submissions/CIK{args.cik}.json", refresh=args.refresh)]
    if not args.recent_only:
        for entry in snapshots[0].payload.get("filings", {}).get("files", []):
            name = entry["name"]
            if not re.fullmatch(r"CIK[0-9]{10}-submissions-[0-9]+\.json", name):
                raise ValueError("Unexpected historical submissions filename")
            snapshots.append(client.fetch_json(f"https://data.sec.gov/submissions/{name}", refresh=args.refresh))
    facts = client.fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{args.cik}.json", refresh=args.refresh)
    if normalize_cik(str(facts.payload.get("cik", ""))) != args.cik:
        raise ValueError("Company Facts issuer mismatch")
    built_at = utc_text(datetime.now(timezone.utc))
    # Each row is bounded by its own source observation, never today's run time.
    rows_by_accession = {}
    source_records = {}
    for snapshot in snapshots:
        rows = build_ledger(args.cik, [snapshot.payload], observed_at=snapshot.observed_at_utc,
                            processing_delay_seconds=args.processing_delay_seconds)
        for row in rows:
            key = row["accession_number"]
            if key in source_records:
                # Compare source records through the common ledger fields, not observation metadata.
                prior = source_records[key]
                compare_keys = ("form", "filing_date", "report_date", "accepted_at_utc", "primary_document")
                if any(prior[k] != row[k] for k in compare_keys):
                    raise ValueError(f"Conflicting accession across pages: {key}")
                # Keep the earliest observation; equal instants retain the first source.
                if parse_timestamp(prior["observed_at_utc"]) <= parse_timestamp(row["observed_at_utc"]):
                    continue
            source_records[key] = row.copy()
            row["submissions_snapshot_sha256"] = snapshot.sha256
            rows_by_accession[key] = row
    rows = [rows_by_accession[key] for key in sorted(rows_by_accession)]
    raw = ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + ("\n" if rows else "")).encode()
    out = args.output_root / "processed" / args.cik / (built_at.replace(":", "-") + f"-{os.getpid()}")
    out.mkdir(parents=True, exist_ok=False)
    (out / "filing_ledger.jsonl").write_bytes(raw)
    manifest = {"schema_version": "sec-ingestion-v1", "issuer_cik": args.cik, "built_at_utc": built_at,
                "row_count": len(rows), "ledger_sha256": hashlib.sha256(raw).hexdigest(),
                "history_scope": "recent_only" if args.recent_only else "all_declared_pages",
                "processing_delay_seconds": args.processing_delay_seconds,
                "sources": [{"url": s.source_url, "sha256": s.sha256, "observed_at_utc": s.observed_at_utc,
                             "raw_path": str(s.path), "from_cache": s.from_cache} for s in [*snapshots, facts]],
                "limitations": ["Company Facts is a raw snapshot, not normalized as-of financial features",
                                "Acceptance metadata is a conservative availability proxy, not measured dissemination latency",
                                "No exchange-session mapping, Form 4 XML parsing, or forecasts yet"]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"CIK {args.cik}: {len(rows)} filings; ledger and manifest: {out}")


if __name__ == "__main__":
    main()
