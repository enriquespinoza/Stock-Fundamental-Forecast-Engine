"""Create an auditable item index for one locally archived 8-K document."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from stock_analyzer.eight_k import parse_8k
from stock_analyzer.ledger import parse_timestamp, utc_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--accession", required=True)
    parser.add_argument("--document", type=Path, required=True,
                        help="Locally archived primary 8-K document bytes")
    parser.add_argument("--observed-at", required=True,
                        help="Timezone-aware time this document was acquired")
    parser.add_argument("--output-root", type=Path, default=Path("data/normalized"))
    args = parser.parse_args()

    ledger_bytes = args.ledger.read_bytes()
    ledger = [json.loads(line) for line in ledger_bytes.decode("utf-8").splitlines() if line.strip()]
    matches = [row for row in ledger if row.get("accession_number") == args.accession]
    if len(matches) != 1:
        raise ValueError("Exactly one matching accession must exist in the ledger")
    raw = args.document.read_bytes()
    observed = utc_text(parse_timestamp(args.observed_at))
    result = parse_8k(raw, matches[0], source_observed_at=observed)

    stamp = utc_text(datetime.now(timezone.utc)).replace(":", "-")
    out = args.output_root / "8k" / f"{stamp}-{os.getpid()}"
    out.mkdir(parents=True, exist_ok=False)
    normalized = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (out / "normalized.json").write_bytes(normalized)
    manifest = {
        "schema_version": "8k-item-index-run-v1",
        "command": "index_8k_filing",
        "ledger_path": str(args.ledger),
        "ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        "source": {"path": str(args.document),
                   "sha256": hashlib.sha256(raw).hexdigest(),
                   "observed_at_utc": observed},
        "output": {"path": str(out / "normalized.json"),
                   "sha256": hashlib.sha256(normalized).hexdigest()},
        "research_status": "INDEX_ONLY_NO_FORECAST",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"8-K item index -> {out}")
    print(f"Items: {result['item_count']}; historical eligible: {result['historical_research_eligible']}")


if __name__ == "__main__":
    main()
