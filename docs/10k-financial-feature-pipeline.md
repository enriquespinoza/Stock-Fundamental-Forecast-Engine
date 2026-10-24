# Point-in-time 10-K and 10-Q financial feature pipeline

## What this pipeline evaluates

The normalized Company Facts history contains XBRL values disclosed in SEC filings. The research join now emits three distinguishable financial views for every Form 4 event:

1. The existing latest-as-of financial facts, regardless of form.
2. A 10-K view selected only from 10-K/10-K/A facts: duration facts must be annual; instant facts are retained as filed.
3. A 10-Q view selected only from 10-Q/10-Q/A facts: duration facts must be single-quarter facts; instant facts are retained as filed.

Each view preserves the source accession, form, tag, period, and availability time. Form-family version selection is independent, so a subsequent 10-Q comparative value cannot overwrite the prior 10-K feature. Year-to-date facts are not mislabeled as quarterly. The model derives margins and balance-sheet ratios only when numerator and denominator have the same exact period and unit. Year-over-year growth features and narrative/MD&A features are not included yet.

The `OperatingIncomeLoss` US-GAAP mapping already exists as `operating_income`. For the new annual series, inspect `financial_operating_income_10k_value` and its paired `financial_operating_income_10k_fact_form`, `_tag`, `_period_start`, `_period_end`, `_fact_accession_number`, and `_fact_available_after_utc` columns. A missing value is a coverage gap, not zero. A row from a 10-Q belongs to the separate `operating_income_10q` feature.

## Rebuild after updating the code

Run from the repository root. `EVENTS` must be the exact event CSV used by the existing return-label run:

```bash
EVENTS="data/processed/form4_events/<event-run>/candidate_transactions.csv"
PRICES="data/raw/market/<label-price-snapshot>.csv"
PRICE_MANIFEST="${PRICES%.csv}.manifest.json"

python scripts/join_form4_financial_universe.py \
  --events "$EVENTS" \
  --financials-root data/normalized/financials

FINANCIAL_FEATURES=$(ls -t data/processed/form4_financial_joins/*/event_financial_features.csv | head -n 1)
LABELS="data/processed/form4_return_labels/<matching-label-run>/event_return_labels.csv"

python scripts/build_event_research_dataset.py \
  --events "$EVENTS" \
  --financial-features "$FINANCIAL_FEATURES" \
  --labels "$LABELS"

RESEARCH=$(ls -t data/processed/form4_research_datasets/*/event_research_dataset.csv | head -n 1)
python scripts/evaluate_sec_signal_models.py \
  --research-dataset "$RESEARCH" \
  --prices "$PRICES" \
  --price-manifest "$PRICE_MANIFEST"
```

The research-dataset builder checks that the events, financial join, and labels refer to the same event snapshot. The evaluator checks that the price CSV exactly matches the hash in the label manifest. If either check fails, select the matching upstream run rather than bypassing the check.

## Interpreting the model tracks

The evaluator reports `price_only`, `price_plus_10k`, `price_plus_10q`, `price_plus_insider_10k`, `price_plus_insider_10q`, the existing mixed `price_plus_financial`, and `price_plus_all_sec`. The family tracks include signed-log fact levels plus period-aligned margins and balance-sheet ratios. The 10-K/10-Q comparisons use identical chronological splits and target events. A missing family-specific fact is explicitly encoded as unavailable; the model does not substitute another filing family or zero accounting values.

This is an exploratory selected-cohort test. The available manual financial review has not validated every fact mapping, and the current cohort is selected from a 2026 S&P 500 snapshot. The 10-K pipeline here evaluates structured XBRL financial facts, not the full 10-K text, MD&A, risk factors, or management guidance. It does not provide evidence of a generalizable edge or permission to deploy forecasts. All outputs remain research-only.
