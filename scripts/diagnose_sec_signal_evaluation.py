from __future__ import annotations

import argparse
from pathlib import Path

from stock_analyzer.sec_signal_diagnostics import diagnose_sec_signal_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report holdout performance and 10-K/10-Q coverage by issuer and event year."
    )
    parser.add_argument("--evaluation-run", type=Path, required=True,
                        help="Run directory containing holdout predictions and manifest")
    parser.add_argument("--research-dataset", type=Path, required=True,
                        help="Exact event research CSV used by the evaluation run")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/sec_signal_diagnostics"))
    args = parser.parse_args()
    result = diagnose_sec_signal_evaluation(args.evaluation_run, args.research_dataset, args.output_root)
    print(f"Diagnostic report -> {result['output_dir']}")
    print(f"Holdout prediction rows: {result['summary']['holdout_event_count']}")
    print("Research only; subgroup results are descriptive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
