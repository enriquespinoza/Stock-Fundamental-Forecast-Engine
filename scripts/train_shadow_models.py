"""Train and freeze paired research-only return models."""

import argparse
from pathlib import Path
import sys

from stock_analyzer.shadow_evaluation import train_shadow_model_pair


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dataset", type=Path, required=True)
    parser.add_argument("--actual-returns", type=Path, required=True,
                        help="Matched TimesFM-origin realized-return CSV")
    parser.add_argument("--training-cutoff-utc", required=True,
                        help="Only events and target closes before this timezone-aware cutoff may train")
    parser.add_argument("--timesfm-feature-start-utc", required=True,
                        help="Start of prospective feature capture using the pinned TimesFM checkpoint")
    parser.add_argument("--code-revision", required=True,
                        help="Full hexadecimal source-control commit ID for this run")
    parser.add_argument("--frozen-at-utc",
                        help="Model freeze time; defaults to current UTC time")
    parser.add_argument("--minimum-training-rows", type=int, default=40)
    parser.add_argument("--primary-start-year", type=int, default=2010)
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/shadow_models"))
    args = parser.parse_args()
    try:
        result = train_shadow_model_pair(
            args.research_dataset, args.actual_returns, args.output_root,
            training_cutoff_utc=args.training_cutoff_utc,
            timesfm_feature_start_utc=args.timesfm_feature_start_utc,
            code_revision=args.code_revision,
            frozen_at_utc=args.frozen_at_utc,
            minimum_training_rows=args.minimum_training_rows,
            primary_start_year=args.primary_start_year,
        )
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print(f"Shadow model training failed: {error}", file=sys.stderr)
        return 1
    print(f"Frozen paired models -> {result['output_dir']}")
    print(f"Training rows by horizon: { {h: v['training_row_count'] for h, v in result['summary']['horizon_training'].items()} }")
    print(f"Frozen at: {result['summary']['frozen_at_utc']}")
    print("Research only; models are not connected to portfolio or execution systems.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
