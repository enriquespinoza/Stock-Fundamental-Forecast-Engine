"""Automated consistency audit for sampled 10-Q/10-K facts."""

import argparse
from pathlib import Path

from stock_analyzer.financial_fact_validation import validate_financial_fact_sample


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True,
                        help="financial_fact_review_sample.csv")
    parser.add_argument("--output", type=Path,
                        help="Separate audit CSV; defaults beside the sample")
    args = parser.parse_args()
    output = args.output or args.sample.with_name("financial_fact_automated_audit.csv")
    result = validate_financial_fact_sample(args.sample, output)
    print(f"Financial fact rows audited: {result['input_rows']}")
    print(f"Unique accessions: {result['unique_accessions']}")
    print(f"Automated statuses: {result['automated_status_counts']}")
    print(f"Audit CSV: {result['output_csv']}")
    print(result["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
