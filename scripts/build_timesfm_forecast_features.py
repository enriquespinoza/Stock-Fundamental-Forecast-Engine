"""Create point-in-time TimesFM 2.5 forecast features for research."""

import argparse
from pathlib import Path
import sys

from stock_analyzer.timesfm_features import build_timesfm_features


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="candidate_transactions.csv from build_form4_events.py")
    parser.add_argument("--prices", type=Path, required=True, help="Immutable adjusted-close CSV")
    parser.add_argument("--price-manifest", type=Path, required=True, help="Matching downloader manifest JSON")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/timesfm_features"))
    parser.add_argument("--context-length", type=int, default=512)
    parser.add_argument("--min-context", type=int, default=32)
    parser.add_argument("--checkpoint-revision", required=True,
                        help="Full 40-character immutable Hugging Face checkpoint commit SHA")
    args = parser.parse_args()
    try:
        result = build_timesfm_features(args.events, args.prices, args.price_manifest,
            args.output_root, context_length=args.context_length, min_context=args.min_context,
            checkpoint_revision=args.checkpoint_revision)
    except (ValueError, RuntimeError, OSError) as error:
        print(f"TimesFM feature build failed: {error}", file=sys.stderr)
        return 1
    print(f"TimesFM features -> {result['output_dir']}")
    print(f"Events: {result['summary']['event_count']}")
    print(f"Statuses: {result['summary']['forecast_status_counts']}")
    print("Research only; no forecasts are sent to portfolio or execution systems.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
