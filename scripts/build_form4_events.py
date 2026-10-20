"""Export point-in-time eligible Form 4 purchase/sale transaction events."""

import argparse
import json
from pathlib import Path

from stock_analyzer.form4_events import build_form4_event_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-root", type=Path, default=Path("data/normalized"))
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/form4_events"))
    args = parser.parse_args()

    result = build_form4_event_dataset(args.normalized_root, args.output_root)
    summary = result["summary"]
    print(f"Normalized accessions: {summary['unique_normalized_accessions']}")
    print(f"Candidate transaction events: {summary['candidate_event_count']}")
    print(f"Buys: {summary['candidate_buy_count']}; sales: {summary['candidate_sale_count']}")
    print(f"Excluded transaction rows: {sum(summary['excluded_transaction_counts'].values())}")
    print(f"Event dataset -> {result['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
