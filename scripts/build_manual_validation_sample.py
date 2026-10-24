"""Build stratified human-review queues for 8-K and 10-Q/10-K extraction."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from stock_analyzer.manual_validation_sample import build_manual_validation_sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-8k-root", type=Path, default=Path("data/normalized/8k"))
    parser.add_argument("--financials-root", type=Path, default=Path("data/normalized/financials"))
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/manual_validation"))
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--8k-per-stratum", dest="eight_k_per_stratum", type=int, default=2)
    parser.add_argument("--financial-facts-per-stratum", type=int, default=1)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = args.output_root / f"{stamp}-seed{args.seed}"
    result = build_manual_validation_sample(
        args.normalized_8k_root, args.financials_root, out,
        seed=args.seed, eight_k_per_stratum=args.eight_k_per_stratum,
        financial_facts_per_stratum=args.financial_facts_per_stratum,
    )
    print(f"8-K filing review rows: {result['sample_counts']['8k_filing_units']}")
    print(f"10-Q/10-K fact review rows: {result['sample_counts']['financial_fact_rows']}")
    print(f"Validation sample -> {out}")
    print("Manual review required; this sample is not return-analysis data.")


if __name__ == "__main__":
    main()
