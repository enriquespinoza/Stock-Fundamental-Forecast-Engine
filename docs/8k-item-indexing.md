# 8-K item indexing (research groundwork)

The first broad-filing implementation indexes visible Item references in one archived 8-K or 8-K/A primary document. It records the issuer CIK, accession, filing and acceptance times, historical and live availability fields, amendment status, item codes, a broad item family, raw source SHA-256, and actual local source acquisition time.

This is an index, not a semantic event classifier. Mention counts can include a table of contents and the body. The parser deliberately does not infer event direction, materiality, or market impact from an item code. Unknown historical acceptance times stay ineligible for historical research. No forecast or order is produced.

## Run

Acquire and archive the primary document first. Pass the timestamp when those exact bytes were acquired; file modification time is not used as a substitute.

```sh
python scripts/index_8k_filing.py \
  --ledger data/processed/0000320193/<ledger-run>/filing_ledger.jsonl \
  --accession 0000000001-26-000001 \
  --document data/raw/sec/8k/<accession>/primary.html \
  --observed-at 2026-10-22T12:03:00Z
```

The command writes `data/normalized/8k/<run>/normalized.json` and `manifest.json`. The manifest hashes the ledger, source bytes, and normalized output. HTML viewer pages that do not identify as an 8-K document are rejected instead of being treated as filing content.

## Scope limits and next validation

This first parser handles primary document HTML/text and visible item references. It does not fetch filings, parse exhibits, deduplicate same-time accessions, or classify narrative beyond the item family map. Before adding outcome analysis, validate the parser against a stratified set of 8-K and 8-K/A fixtures, compare extracted items with SEC filing headers, audit unsupported responses, and connect events to point-in-time security identifiers and executable market sessions. Keep family-level results exploratory until the historical universe and release-time coverage pass the study gates in [the filing-signal protocol](sec-filing-signal-study.md).
