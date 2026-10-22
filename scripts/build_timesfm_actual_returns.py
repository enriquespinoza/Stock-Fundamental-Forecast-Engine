"""Build matched realized returns and forecast-error metrics for TimesFM research."""

import argparse
from pathlib import Path
import sys

from stock_analyzer.timesfm_actual_returns import build_timesfm_actual_returns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesfm-features", type=Path, required=True,
                        help="event_timesfm_features.csv produced by build_timesfm_forecast_features.py")
    parser.add_argument("--prices", type=Path, required=True,
                        help="The exact price CSV recorded in the TimesFM feature manifest")
    parser.add_argument("--price-manifest", type=Path, required=True,
                        help="Manifest for the exact price CSV used for TimesFM features")
    parser.add_argument("--primary-start-year", type=int, default=2010,
                        help="First event-availability year in reported evaluation metrics (default: 2010)")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/timesfm_actual_returns"))
    args = parser.parse_args()
    try:
        result = build_timesfm_actual_returns(args.timesfm_features, args.prices,
                                              args.price_manifest, args.output_root,
                                              primary_start_year=args.primary_start_year)
    except (ValueError, OSError, KeyError) as error:
        print(f"TimesFM actual-return build failed: {error}", file=sys.stderr)
        return 1
    print(f"Matched return rows: {result['summary']['event_count']}")
    print(f"Primary sample events: {result['summary']['primary_sample_event_count']}")
    print(f"Horizon status counts: {result['summary']['horizon_status_counts']}")
    print(f"Event-weighted primary metrics: {result['summary']['primary_sample_metrics_event_weighted']}")
    print(f"Unique-origin primary metrics: {result['summary']['primary_sample_metrics_unique_origins']}")
    print(f"Matched actual returns -> {result['output_dir']}")
    print("Research only; no forecasts are sent to portfolio or execution systems.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
