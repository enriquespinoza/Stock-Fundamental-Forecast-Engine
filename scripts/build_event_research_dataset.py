"""Build a one-row-per-event dataset from PIT financial features and return labels."""

import argparse
from pathlib import Path

from stock_analyzer.research_dataset import build_event_research_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True,
                        help="candidate_transactions.csv used by both upstream runs")
    parser.add_argument("--financial-features", type=Path, required=True,
                        help="event_financial_features.csv from join_form4_financials.py")
    parser.add_argument("--labels", type=Path, required=True,
                        help="event_return_labels.csv from build_forward_return_labels.py")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/form4_research_datasets"))
    args = parser.parse_args()
    result = build_event_research_dataset(args.events, args.financial_features,
                                          args.labels, args.output_root)
    summary = result["summary"]
    print(f"Events assembled: {summary['event_count']}")
    print(f"Financial metrics per event: {summary['financial_metric_count']}")
    print(f"Primary-sample events: {summary['primary_sample_event_count']}")
    print(f"Research dataset -> {result['output_dir']}")
    print("Forecast status: LABELS_AND_FEATURES_NO_FORECAST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
