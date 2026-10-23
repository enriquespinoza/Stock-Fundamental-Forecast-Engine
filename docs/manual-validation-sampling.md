# Stratified manual validation sample

## What this sample is

A manual validation sample is a deliberately selected set of archived SEC records that a person checks against the source filing. It measures whether our extraction and normalization are faithful to the filing. It is separate from the return-analysis dataset and does not establish that a filing feature predicts returns.

**Stratified** means first dividing the available records into meaningful groups, then randomly selecting records within each group. Here, 8-K filings are grouped by target item family (Item 2.02, Item 5.02, or neither), filing form (8-K or 8-K/A), and five-year filing-date band. The `neither` group is a negative-coverage check for missed target item sections. Financial fact rows are grouped by metric, form (10-Q, 10-K, or amendment), filing-date band, and period kind. This avoids a sample made up only of the most recent or easiest-to-find filings.

The fixed seed makes the selection reproducible for the same inputs. The manifest records input hashes, population and sample counts by stratum, and shortfalls when a group has fewer records than requested. The sample is not automatically representative of all SEC filers: its population is limited to the normalized records available under the supplied roots.

## Build the review queue

From the repository root, run:

```sh
python scripts/build_manual_validation_sample.py \
  --normalized-8k-root data/normalized/8k \
  --financials-root data/normalized/financials \
  --seed 20261023 \
  --8k-per-stratum 2 \
  --financial-facts-per-stratum 1
```

The command writes a unique run directory under `data/processed/manual_validation/` containing:

- `8k_filing_review_sample.csv`: filing-level sample, target item family, source/normalized paths and hashes, availability context, and blank human annotation fields.
- `financial_fact_review_sample.csv`: fact-level sample with CIK, accession, metric, tag, value, unit, period, availability, source filing URL, source snapshot hash, and blank observed-value/review fields.
- `manifest.json` and `summary.json`: seed, input hashes, sampling method, population/sample counts, and per-stratum counts.

For the 8-K sample, review each selected source filing, including filings in the `NO_TARGET_ITEM` group. Record the item codes actually present and describe the substantive event, if any, in the blank review columns. Then run `scripts/validate_8k_index.py` for sampled filings to generate the item-reference review CSV and compare the indexer's references against the source. Manual confirmation means the visible item reference was checked; it does not by itself verify an event label.

For each sampled 10-Q/10-K fact, use `source_url` to inspect the filing and compare the selected XBRL tag, reported value, unit, and period. Record the observed values and review status in the sample CSV. This checks the normalized fact mapping for represented rows; it does not test whether the pipeline missed facts that are absent from the selected sample or unsupported by its current tag mapping.

Do not edit source data or hashes. Preserve the generated sample and manifest unchanged; save completed annotations as a separate reviewed copy so the original selection remains auditable. Treat annotation disagreements as extraction/definition issues to resolve before any return correlation or forecast testing.
