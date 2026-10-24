"""Evaluate price-only and SEC-feature return models on a chronological holdout."""

import argparse
from pathlib import Path
import sys

from stock_analyzer.sec_signal_models import evaluate_sec_signal_models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dataset", type=Path, required=True,
                        help="Manifest-backed event_research_dataset.csv")
    parser.add_argument("--prices", type=Path, required=True,
                        help="Exact immutable price CSV used to create the return labels")
    parser.add_argument("--price-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/sec_signal_model_evaluations"))
    parser.add_argument("--train-fraction", type=float, default=0.8,
                        help="Fraction of unique event dates before the chronological holdout")
    parser.add_argument("--ridge-alpha", type=float, default=10.0,
                        help="Predeclared Ridge regularization; no holdout tuning is performed")
    parser.add_argument("--minimum-training-rows", type=int, default=40)
    parser.add_argument("--minimum-test-rows", type=int, default=20)
    parser.add_argument("--bootstrap-replicates", type=int, default=1000)
    args = parser.parse_args()
    try:
        result = evaluate_sec_signal_models(
            args.research_dataset, args.prices, args.price_manifest, args.output_root,
            train_fraction=args.train_fraction, ridge_alpha=args.ridge_alpha,
            minimum_training_rows=args.minimum_training_rows,
            minimum_test_rows=args.minimum_test_rows,
            bootstrap_replicates=args.bootstrap_replicates,
        )
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print(f"SEC signal evaluation failed: {error}", file=sys.stderr)
        return 1
    print(f"SEC signal evaluation -> {result['output_dir']}")
    for horizon, detail in result["summary"]["horizons"].items():
        print(f"{horizon}: train={detail['training_event_count_after_purge']} "
              f"holdout={detail['holdout_event_count']} cutoff={detail['chronological_holdout_cutoff_date']}")
        for model_name, metrics in detail["metrics"].items():
            print(f"  {model_name}: MAE={metrics['mae']:.6f} RMSE={metrics['rmse']:.6f} "
                  f"Spearman={metrics['spearman_ic']}")
    print("Research only; results are exploratory for the selected pilot cohort.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
