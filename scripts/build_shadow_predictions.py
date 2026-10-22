"""Log paired prospective shadow predictions from frozen model artifacts."""

import argparse
from pathlib import Path
import sys

from stock_analyzer.shadow_evaluation import build_shadow_predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dataset", type=Path, required=True,
                        help="New point-in-time event dataset with TimesFM features")
    parser.add_argument("--model-dir", type=Path, required=True,
                        help="Frozen paired model run directory")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/shadow_predictions"))
    args = parser.parse_args()
    try:
        result = build_shadow_predictions(args.research_dataset, args.model_dir, args.output_root)
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print(f"Shadow prediction build failed: {error}", file=sys.stderr)
        return 1
    print(f"Paired predictions: {result['summary']['prediction_count']}")
    print(f"Prediction CSV -> {result['csv_path']}")
    print("Research only; predictions are not sent to the optimizer or execution engine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
