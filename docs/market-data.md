# Market data and forward-return labels

## Download AAPL and SPY prices

Install the optional market-data tools in the project virtual environment:

```bash
python -m pip install -e ".[market-data]"
```

Then create an immutable daily snapshot from 2010 through the latest completed
session:

```bash
python scripts/download_market_prices.py
```

The script downloads AAPL and SPY sequentially from Yahoo Finance using
`auto_adjust=False`, so `Adj Close` is explicit. It matches daily bars to the
XNYS exchange schedule to record UTC close times, including early closes. The
CSV and a manifest are written to `data/raw/market/`; each run uses a new
timestamped filename, and the manifest records package versions, acquisition
time, row count, and the CSV SHA-256. Do not edit or replace a snapshot after
the manifest is created. To choose another range or output directory, use
`--start-date`, `--end-date` (exclusive), and `--output-root`.

Yahoo Finance is an initial research source accessed through the community
`yfinance` package. Preserve the provider and adjustment methodology in every
run; do not mix its prices with another source in one snapshot.

## Price input contract

The return-label command consumes the daily price CSV produced above (or another
CSV meeting this contract). It does not download market data. The file must
have exactly these columns:

```csv
symbol,session_date,close_at_utc,adjusted_close
AAPL,2026-01-02,2026-01-02T21:00:00Z,100.00
SPY,2026-01-02,2026-01-02T21:00:00Z,200.00
```

- `symbol`: uppercase security symbol, including the configured benchmark (SPY by default).
- `session_date`: exchange session date in `YYYY-MM-DD` format.
- `close_at_utc`: timezone-aware timestamp of that session's actual close, including early closes. Do not derive it by assuming every session closes at 4:00 p.m. Eastern.
- `adjusted_close`: positive price adjusted consistently for splits and cash distributions, as appropriate to the provider's documented total-return methodology. Keep one provider and one adjustment policy for the whole file.

The command rejects duplicate symbol/session rows, invalid timestamps, and nonpositive prices. Preserve the original downloaded file as an immutable raw input; the output manifest records its SHA-256, provider description, acquisition timestamp, and declared price basis. The source description and adjustment basis are operator-supplied and should match the provider's documentation.

## Run labels

Use the candidate event file produced by `scripts/build_form4_events.py` and
the raw price CSV. Copy the exact path and `Observed at UTC` value printed by
the downloader:

```bash
python scripts/build_forward_return_labels.py \
  --events "data/processed/form4_events/EVENT_RUN/candidate_transactions.csv" \
  --prices "data/raw/market/CSV_PATH_FROM_DOWNLOADER.csv" \
  --price-source "Yahoo Finance via yfinance; see snapshot manifest" \
  --price-basis "Yahoo Adj Close; auto_adjust=False; see snapshot manifest" \
  --prices-observed-at "OBSERVED_AT_UTC_FROM_DOWNLOADER"
```

Replace `EVENT_RUN`, the CSV path, and observation timestamp with the actual
values. `--prices-observed-at` is when this exact price snapshot was acquired,
not the date of the latest bar.

The labeler selects the first issuer session close whose actual UTC close timestamp is strictly later than `historical_available_after_utc`. The benchmark close must exist for that same starting session and also be strictly after event availability. For each horizon, it advances 1, 5, or 20 issuer trading rows and requires an SPY row on the resulting session date. It calculates:

- `forward_return_Nd`: issuer adjusted-price simple return.
- `benchmark_return_Nd`: configured benchmark (SPY by default) adjusted-price simple return over the same dates.
- `excess_return_Nd`: issuer return minus benchmark return, in percentage-point units as a decimal fraction.

For example, `0.025` means 2.5% return, and an excess return of `0.01` means the issuer outperformed SPY by one percentage point. This arithmetic excess return is not a risk-adjusted alpha estimate.

The output is a separate, immutable run under `data/processed/form4_return_labels/`, with a CSV, summary, and manifest. Events before 2010 remain represented but have `primary_sample_eligible=false`; 2009 can be examined as a sparse boundary year. Incomplete future horizons have null labels and an explicit status. Missing prices are never filled. Join labels back to the long-form financial feature file using `event_id`; do not join on ticker and date alone.

## Assemble an event-level research table

After generating both the point-in-time financial join and the return labels,
combine them into one row per insider transaction. The assembler verifies that
both outputs reference the exact same event CSV hash, all three datasets have
matching event IDs, financial facts were available by the event timestamp, and
the input manifests and CSV hashes agree.

```bash
python scripts/build_event_research_dataset.py \
  --events "data/processed/form4_events/EVENT_RUN/candidate_transactions.csv" \
  --financial-features "data/processed/form4_financial_joins/FINANCIAL_RUN/event_financial_features.csv" \
  --labels "data/processed/form4_return_labels/LABEL_RUN/event_return_labels.csv"
```

Replace the run-directory placeholders with the exact runs you built. The
output under `data/processed/form4_research_datasets/` retains each original
event field, metric-specific financial values and fact provenance, and label
status/returns. It does not calculate correlations or train forecasts. Filter
to `label_primary_sample_eligible=true` and the horizon-specific status `OK`
only when evaluating that horizon; keep the full output as the auditable base.

## Interpretation limits

These are outcome labels for historical research, not forecasts or recommendations. Labels are available only after their full horizon has elapsed. At each eventual training or forecast cutoff, rows with outcomes not yet known must be excluded. The event features still must be restricted to information available by the Form 4 public-availability timestamp. The first AAPL run checks plumbing and timestamp behavior; it cannot establish a predictive relationship. Broader, point-in-time security coverage and chronological out-of-sample testing are required before any general claim.
