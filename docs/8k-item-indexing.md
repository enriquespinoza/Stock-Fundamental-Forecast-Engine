# 8-K item indexing (research groundwork)

The indexer downloads or reads one archived 8-K/8-K/A primary document and records visible Item references. It preserves issuer CIK, accession, form, filing and acceptance times, historical/live availability, amendment status, item codes, source SHA-256, and the actual document acquisition time. Downloads use the identified, rate-limited SEC client and an immutable local cache.

This is an item index, not a semantic event classifier. Mention counts can include a table of contents and the body. Item labels do not establish event direction, materiality, or market impact. Unknown historical acceptance times remain ineligible for historical research. No forecast or order is produced.

## Index the newest filing in a ledger (Git Bash)

Set `SEC_USER_AGENT` to an application name and your real contact email if it is not already set. Then select the newest ledger for one issuer and download/index its latest 8-K:

```sh
CIK=0000320193
LEDGER=$(find "data/processed/$CIK" -type f -name filing_ledger.jsonl | sort | tail -n 1)
if [ -z "$LEDGER" ]; then echo "No filing ledger found for CIK $CIK"; exit 1; fi

python scripts/index_8k_filing.py \
  --ledger "$LEDGER" \
  --latest \
  --download
```

Use `--accession 0000000000-00-000000` instead of `--latest` to select a specific accession. The script obtains the primary document path from the ledger, checks that the URL remains on SEC Archives, rate-limits the request, caches the raw bytes with a checksum, and records the acquisition time automatically.

For an already archived local document, use `--document PATH --observed-at TIME`; the timestamp must describe when those exact bytes were acquired. File modification time is not used as a substitute.

The command writes `data/normalized/8k/<run>/normalized.json` and `manifest.json`. Both the source and normalized output are hashed.

## Scope limits and next validation

This first slice indexes primary document text/HTML and visible item references. It does not fetch exhibits, cluster same-time accessions, extract financial changes from 10-K/10-Q, or classify narrative meaning. Before outcome analysis, validate a stratified set of 8-K and 8-K/A fixtures against filing headers, audit coverage/unsupported responses, and connect events to point-in-time securities and executable market sessions. Treat findings as exploratory until the historical-universe and release-time gates in [the filing-signal protocol](sec-filing-signal-study.md) pass.
