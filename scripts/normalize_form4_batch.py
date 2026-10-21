"""Normalize recent, unprocessed Form 4 filings sequentially."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def already_normalized(output_root: Path) -> set[str]:
    accessions = set()
    for path in (output_root / "form4").glob("*/normalized.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            accession = data.get("filing", {}).get("accession_number")
            if accession:
                accessions.add(accession)
        except (OSError, json.JSONDecodeError):
            continue
    return accessions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10,
                        help="Maximum number of newest unprocessed filings (default: 10)")
    parser.add_argument("--output-root", type=Path, default=Path("data/normalized"))
    parser.add_argument("--summary-root", type=Path,
                        default=Path("data/processed/form4_batch_summaries"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/raw/sec"))
    parser.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT", ""))
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be at least 1")
    if not args.user_agent:
        parser.error("Set SEC_USER_AGENT or provide --user-agent")

    ledger = [
        json.loads(line)
        for line in args.ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    existing = already_normalized(args.output_root)

    filings = []
    unsupported_source_format = []
    seen = set()
    for row in ledger:
        accession = row.get("accession_number")
        document = row.get("primary_document") or ""
        if row.get("form") not in ("4", "4/A") or not accession or accession in existing or accession in seen:
            continue
        seen.add(accession)
        if not document.lower().endswith(".xml"):
            unsupported_source_format.append({
                "accession_number": accession,
                "filing_date": row.get("filing_date"),
                "form": row["form"],
                "primary_document": document,
                "status": "UNSUPPORTED_SOURCE_FORMAT",
            })
            continue
        filings.append(row)

    filings.sort(
        key=lambda row: (row.get("filing_date", ""), row["accession_number"]),
        reverse=True,
    )
    selected = filings[:args.limit]
    if not selected and not unsupported_source_format:
        print("No unprocessed Form 4 filings found.")
        return 0

    single_filing_script = Path(__file__).with_name("normalize_sec_data.py")
    results = []

    for index, filing in enumerate(selected):
        accession = filing["accession_number"]
        command = [
            sys.executable, str(single_filing_script), "form4",
            "--ledger", str(args.ledger),
            "--accession", accession,
            "--download",
            "--output-root", str(args.output_root),
            "--cache-dir", str(args.cache_dir),
            "--user-agent", args.user_agent,
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()

        if stdout:
            print(stdout)
        if stderr:
            print(stderr, file=sys.stderr)

        results.append({
            "accession_number": accession,
            "filing_date": filing.get("filing_date"),
            "form": filing["form"],
            "return_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        })

        # The existing CLI starts a new SEC client for each filing.
        # Keep requests sequential and at least 0.5 seconds apart.
        if index < len(selected) - 1:
            time.sleep(0.6)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.summary_root.mkdir(parents=True, exist_ok=True)
    summary_path = args.summary_root / f"form4-batch-{stamp}-{os.getpid()}.json"
    summary = {
        "completed_at_utc": stamp,
        "ledger_path": str(args.ledger),
        "selected_count": len(selected),
        "success_count": sum(item["return_code"] == 0 for item in results),
        "failure_count": sum(item["return_code"] != 0 for item in results),
        "unsupported_source_format": unsupported_source_format,
        "results": results,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Batch summary -> {summary_path}")
    print(f"Successes: {summary['success_count']}; failures: {summary['failure_count']}")
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
