"""Audit Form 4 normalized outputs, source hashes, transaction coverage, and batch status."""

import argparse
import json
from pathlib import Path

from stock_analyzer.form4_audit import audit_form4_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--normalized-root", type=Path, default=Path("data/normalized"))
    parser.add_argument("--batch-summary", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/processed/form4_history_audit.json"))
    args = parser.parse_args()

    report = audit_form4_history(args.ledger, args.normalized_root,
                                 batch_summary_path=args.batch_summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Form 4 filings in ledger: {report['ledger']['form4_count']}")
    print(f"Normalized unique accessions: {report['artifacts']['unique_normalized_accessions']}")
    print(f"Duplicate normalized artifacts: {report['artifacts']['duplicate_artifact_count']}")
    print(f"Missing normalized accessions: {len(report['artifacts']['missing_accessions'])}")
    print(f"Missing XML-supported accessions: {len(report['artifacts']['missing_supported_accessions'])}")
    print(f"Transactions audited: {report['filings']['transaction_count']}")
    print(f"Batch successes/failures: {report['batch']['success_count']}/{report['batch']['failure_count']}")
    print(f"Unsupported legacy formats: {len(report['batch']['unsupported_source_format'])}")
    print(f"Integrity issues: {len(report['artifacts']['integrity_issues'])}")
    print(f"Audit report: {args.output}")
    return 1 if (report["artifacts"]["integrity_issues"]
                 or report["artifacts"]["missing_supported_accessions"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
