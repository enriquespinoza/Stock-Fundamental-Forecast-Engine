"""Join candidate Form 4 events to the latest eligible financial fact as of each event."""

import argparse
from pathlib import Path

from stock_analyzer.point_in_time import join_form4_events_to_financials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="candidate_transactions.csv")
    parser.add_argument("--financials", type=Path, required=True, help="financials normalized.json with full fact history")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/form4_financial_joins"))
    args = parser.parse_args()

    result = join_form4_events_to_financials(args.events, args.financials, args.output_root)
    summary = result["summary"]
    print(f"Events joined: {summary['event_count']}")
    print(f"Metrics per event: {summary['metric_count']}")
    print(f"Selected as-of fact rows: {summary['selected_fact_rows']}")
    print(f"No eligible fact rows: {summary['missing_metric_rows']}")
    print(f"Financial normalization issues retained: {summary['financials_with_issues']}")
    print(f"Point-in-time join -> {result['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
