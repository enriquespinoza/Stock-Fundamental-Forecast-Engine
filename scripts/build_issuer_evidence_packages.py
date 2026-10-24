from __future__ import annotations

import argparse
from pathlib import Path

from stock_analyzer.evidence_packages import build_evidence_packages


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build point-in-time evidence packages from issuer fundamental briefs."
    )
    parser.add_argument("--briefs", type=Path, required=True,
                        help="issuer_fundamental_briefs.jsonl from build_issuer_fundamental_briefs.py")
    parser.add_argument("--brief-manifest", type=Path,
                        help="Optional manifest.json from the same fundamental brief run")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/issuer_evidence_packages"))
    args = parser.parse_args()
    result = build_evidence_packages(args.briefs, args.output_root,
                                     brief_manifest_path=args.brief_manifest)
    summary = result["summary"]
    print(f"Evidence packages: {summary['package_count']}")
    print(f"Evidence items: {summary['evidence_item_count']}")
    print(f"Evidence packages -> {result['output_dir']}")
    print("Research only; no allocation or execution effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
