"""Create point-in-time 1, 5, and 20-session return labels for Form 4 events."""

import argparse
from pathlib import Path

from stock_analyzer.return_labels import build_forward_return_labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True,
                        help="candidate_transactions.csv from build_form4_events.py")
    parser.add_argument("--prices", type=Path, required=True,
                        help="CSV with symbol,session_date,close_at_utc,adjusted_close")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/form4_return_labels"))
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--price-source", required=True,
                        help="Market data provider and dataset/version description")
    parser.add_argument("--price-basis", required=True,
                        help="Meaning of adjusted_close, including split/dividend treatment")
    parser.add_argument("--prices-observed-at", required=True,
                        help="Timezone-aware timestamp when the immutable price CSV was acquired")
    parser.add_argument("--primary-start-year", type=int, default=2010,
                        help="First event-availability year included in the primary sample")
    args = parser.parse_args()

    result = build_forward_return_labels(
        args.events, args.prices, args.output_root, benchmark=args.benchmark,
        price_source=args.price_source, price_basis=args.price_basis,
        prices_observed_at=args.prices_observed_at,
        primary_start_year=args.primary_start_year,
    )
    summary = result["summary"]
    print(f"Events labeled: {summary['event_count']}")
    print(f"Events in primary sample: {summary['primary_sample_event_count']}")
    print(f"Events with a start close: {summary['events_with_start_close']}")
    for horizon, counts in summary["horizon_status_counts"].items():
        print(f"{horizon} horizon: {counts}")
    print(f"Return labels -> {result['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
