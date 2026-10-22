"""Score matured paired shadow predictions against archived market closes."""

import argparse
from pathlib import Path
import sys

from stock_analyzer.shadow_evaluation import score_shadow_predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--actual-returns", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--price-manifest", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/shadow_scores"))
    args = parser.parse_args()
    try:
        result = score_shadow_predictions(
            args.predictions, args.actual_returns, args.prices, args.price_manifest,
            args.output_root, replicates=args.bootstrap_replicates, seed=args.seed,
        )
    except (ValueError, OSError, KeyError) as error:
        print(f"Shadow score failed: {error}", file=sys.stderr)
        return 1
    print(f"Paired outcomes matched: {result['summary']['matched_outcome_count']}")
    print(f"Metrics: {result['summary']['metrics']}")
    print(f"Cluster confidence intervals: {result['summary']['paired_mae_delta_95pct_cluster_ci']}")
    print(f"Shadow score -> {result['output_dir']}")
    print("Research only; no portfolio or execution systems were accessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
