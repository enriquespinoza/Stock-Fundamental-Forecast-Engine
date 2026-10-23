"""Extract reviewed, point-in-time Item 2.02 and 5.02 text indicators."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from stock_analyzer.eight_k_events import extract_reviewed_8k_features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized", type=Path, required=True, help="normalized.json from the 8-K indexer")
    parser.add_argument("--review-csv", type=Path, required=True, help="Manually completed item_reference_review.csv")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/8k_section_features"))
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = args.output_root / f"{stamp}-{args.normalized.parent.name}"
    result = extract_reviewed_8k_features(args.normalized, args.review_csv, out)
    print(f"Accession: {result['accession_number']}")
    print(f"Section feature rows: {result['event_count']} ({result['item_counts']})")
    print(f"Feature output -> {out}")
    print("Research only; no forecasts or orders.")


if __name__ == "__main__":
    main()
