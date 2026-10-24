"""Join Form 4 events to point-in-time financial histories for multiple issuers."""

import argparse
from pathlib import Path

from stock_analyzer.point_in_time_universe import join_form4_events_to_financial_universe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="candidate_transactions.csv")
    parser.add_argument("--financials-root", type=Path, required=True,
                        help="Root containing manifest-backed financials/*/normalized.json runs")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/form4_financial_joins"))
    args = parser.parse_args()
    result = join_form4_events_to_financial_universe(args.events, args.financials_root, args.output_root)
    summary = result["summary"]
    print(f"Events joined: {summary['event_count']}")
    print(f"Issuers joined: {summary['issuer_count']}")
    print(f"Metrics per event: {summary['metric_count']}")
    print(f"Selected as-of fact rows: {summary['selected_fact_rows']}")
    print(f"Selected general / 10-K / 10-Q rows: "
          f"{summary['selected_base_fact_rows']} / {summary['selected_10k_fact_rows']} / "
          f"{summary['selected_10q_fact_rows']}")
    print(f"No eligible fact rows: {summary['missing_metric_rows']}")
    print(f"Point-in-time universe join -> {result['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
