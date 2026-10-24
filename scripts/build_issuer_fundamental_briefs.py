from __future__ import annotations

import argparse
from pathlib import Path

from stock_analyzer.fundamental_briefs import build_fundamental_briefs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build as-of, descriptive SEC fundamentals briefs for a company universe."
    )
    parser.add_argument("--financials-root", type=Path, required=True,
                        help="Manifest-backed normalized financial history root")
    parser.add_argument("--universe", type=Path,
                        default=Path("configs/universe/pilot_sp500_2026-10-07.csv"),
                        help="CSV with symbol and issuer_cik columns")
    parser.add_argument("--as-of", required=True, help="Timezone-aware UTC or offset timestamp")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/fundamental_briefs"))
    args = parser.parse_args()
    result = build_fundamental_briefs(args.financials_root, args.universe, args.as_of, args.output_root)
    summary = result["summary"]
    print(f"Issuer briefs: {summary['brief_count']} / {summary['universe_count']}")
    print(f"Review signals: {summary['review_signal_count']}")
    print(f"Missing histories: {len(summary['missing_financial_histories'])}")
    print(f"Fundamental briefs -> {result['output_dir']}")
    print("Descriptive prompts only; no effect on allocation or execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
