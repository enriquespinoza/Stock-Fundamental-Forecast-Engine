# Multi-issuer point-in-time research

The AAPL run validates the pipeline plumbing. It is not enough to establish a
general insider-trading or financial-statement return signal. This phase adds a
company-safe financial join for a broader pilot cohort; it does not fit or
evaluate a forecast model.

## Prepare issuer data

For each pilot company, ingest SEC submissions and Company Facts with its CIK,
then normalize the complete Company Facts history using that issuer's ledger.
Keep each normalized run and manifest under `data/normalized/financials/`.
The universe join discovers these runs recursively, verifies their normalized
SHA-256 hashes, and deterministically uses the lexically newest UTC-stamped run
for each CIK. It fails if an event issuer has no verified financial history.

Normalize Form 4 filings for the same companies into `data/normalized/form4/`
and build a fresh candidate event dataset after all selected Form 4 histories
are present. Market-price data must include each issuer symbol plus the chosen
benchmark, such as SPY.

The initial frozen 32-issuer cross-sector pilot is listed in
[`configs/universe/pilot_sp500_2026-10-07.csv`](../configs/universe/pilot_sp500_2026-10-07.csv).
Its scope and survivorship warning are in the paired manifest and
[`docs/universe-expansion.md`](universe-expansion.md). This snapshot supports
pipeline and coverage validation only.

## Join financial facts and build the research table

```bash
python scripts/build_form4_events.py
python scripts/join_form4_financial_universe.py \\
  --events "data/processed/form4_events/<event-run>/candidate_transactions.csv" \\
  --financials-root data/normalized/financials
```

The join writes one row per event and metric to `event_financial_features.csv`.
Its manifest records the event CSV hash and the selected normalized-file hash
for every issuer. Selection is limited to facts whose historical availability
time is no later than that event's public-availability timestamp. Facts from a
different issuer are never considered.

Then use the same event CSV to build forward-return labels from a price snapshot
containing every event symbol and the benchmark, and pass the resulting labels
and financial-feature CSV to `scripts/build_event_research_dataset.py`. This
keeps one event-level row with its point-in-time features and subsequent return
labels.

## Research gate

Begin with a manageable pilot covering different industries, verify issuer and
symbol mappings, event counts, point-in-time coverage, price coverage, and
source manifests, then expand the universe. A pilot selected from current index
members is useful for pipeline testing but has survivorship bias; do not use it
as evidence of historical investable performance. Before testing relationships
or forecasts, define a historical universe that includes delisted companies,
use chronological walk-forward evaluation, compare against simple baselines,
and keep model outputs separate from execution decisions.
