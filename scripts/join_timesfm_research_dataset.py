"""Join TimesFM feature outputs to the manifested event research dataset."""

import argparse
from pathlib import Path
import sys

from stock_analyzer.timesfm_research_join import join_timesfm_research_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dataset", type=Path, required=True,
                        help="event_research_dataset.csv from build_event_research_dataset.py")
    parser.add_argument("--timesfm-features", type=Path, required=True,
                        help="event_timesfm_features.csv from build_timesfm_forecast_features.py")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/form4_timesfm_research"))
    args = parser.parse_args()
    try:
        result = join_timesfm_research_dataset(args.research_dataset, args.timesfm_features, args.output_root)
    except (ValueError, OSError) as error:
        print(f"TimesFM research join failed: {error}", file=sys.stderr)
        return 1
    print(f"Events joined: {result['summary']['event_count']}")
    print(f"TimesFM statuses: {result['summary']['timesfm_forecast_status_counts']}")
    print(f"Research dataset -> {result['output_dir']}")
    print("Research only; this output does not feed portfolio or execution systems.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
