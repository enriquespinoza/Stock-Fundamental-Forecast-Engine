"""Validate one normalized 8-K item index against its archived source bytes."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from stock_analyzer.eight_k_validation import validate_8k_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized", type=Path, required=True,
                        help="normalized.json from index_8k_filing.py")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/8k_validation"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = validate_8k_run(args.normalized, args.output_root, built_at_utc=now)
    print(f"Accession: {result['accession_number']}")
    print(f"Source item counts match index: {result['item_reference_counts_match_source']}")
    print(f"Item references in review file: {result['source_reference_count']}")
    print(f"Manual review: {result['manual_review_status']}")
    print(f"Validation output -> {args.output_root}")


if __name__ == "__main__":
    main()
