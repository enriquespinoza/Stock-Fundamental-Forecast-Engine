"""Record a human-verified financial-fact row in a separate review copy."""

import argparse
from pathlib import Path

from stock_analyzer.manual_validation_annotation import annotate_financial_fact


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record one source-checked fact in a separate reviewed CSV copy."
    )
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--accession", required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--period-start", default="")
    parser.add_argument("--period-end", required=True)
    parser.add_argument("--observed-tag", required=True)
    parser.add_argument("--observed-value", required=True)
    parser.add_argument("--observed-unit", required=True)
    parser.add_argument("--observed-period-start", default="")
    parser.add_argument("--observed-period-end", required=True)
    parser.add_argument("--review-notes", required=True)
    parser.add_argument(
        "--status", choices=("CONFIRMED", "MISMATCH", "UNABLE_TO_VERIFY"),
        default="CONFIRMED",
    )
    parser.add_argument(
        "--source-checked", action="store_true", required=True,
        help="Explicitly attest that you checked the SEC filing itself.",
    )
    args = parser.parse_args()
    output = args.output or args.sample.with_name(
        "financial_fact_review_sample_reviewed.csv"
    )
    result = annotate_financial_fact(
        args.sample,
        output,
        accession_number=args.accession,
        metric=args.metric,
        tag=args.tag,
        period_start=args.period_start,
        period_end=args.period_end,
        observed_tag=args.observed_tag,
        observed_value=args.observed_value,
        observed_unit=args.observed_unit,
        observed_period_start=args.observed_period_start,
        observed_period_end=args.observed_period_end,
        review_notes=args.review_notes,
        status=args.status,
        source_checked=args.source_checked,
    )
    print(f"Reviewed copy -> {result['output_path']}")
    print(f"Rows: {result['row_count']}; updated: {result['annotated_accession_number']} {result['annotated_metric']} ({result['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
