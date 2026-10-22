# Optional TimesFM 2.5 research features

This module adds experimental model features from archived daily adjusted-close
snapshots. It is separate from SEC ingestion, normalization, portfolio
optimization, and order execution. Nothing produced by it is an order or
investment recommendation.

## Install the optional model dependency

TimesFM is intentionally excluded from the default install because PyTorch and
the checkpoint are large. The project extra uses the upstream TimesFM 2.0.2
package, which exposes the TimesFM 2.5 PyTorch model API:

```bash
python -m pip install -e ".[timesfm]"
```

The CLI loads `google/timesfm-2.5-200m-pytorch` lazily on its first run. Set
`--checkpoint-revision` to a pinned Hugging Face revision when reproducibility
requires an immutable checkpoint identifier. The model's pretraining cutoff is
not known from this pipeline, so historical forecast results must not be
presented as leakage-free backtests.

## Build event-level features

Inputs are a manifested Form 4 event CSV, an immutable price snapshot CSV, and
that CSV's manifest. For example:

```bash
python scripts/build_timesfm_forecast_features.py \
  --events "data/processed/form4_events/EVENT_RUN/candidate_transactions.csv" \
  --prices "data/raw/market/PRICE_SNAPSHOT.csv" \
  --price-manifest "data/raw/market/PRICE_SNAPSHOT.manifest.json" \
  --context-length 512 \
  --min-context 32
```

The command writes a timestamped run under `data/processed/timesfm_features/`
with `event_timesfm_features.csv`, `summary.json`, and `manifest.json`. Each
row keeps the event ID and produces 1-, 5-, and 20-session forecast adjusted
closes and price changes relative to the last adjusted close available strictly
before `historical_available_after_utc`. A forecast is skipped with an explicit
status when there are fewer than the requested minimum context rows. Identical
symbol/context inputs are forecast once per run and reused for events sharing
the same price history.

Both source CSV hashes and their manifest hashes are recorded. The output
manifest documents the point-in-time rule, price snapshot observation time,
model ID/revision, horizons, caveats, and the output hash. The code checks the
source row counts and SHA-256 values before inference.

## Join to the event research dataset

Build the financial join, return labels, and base research dataset from the same
candidate event CSV used for the TimesFM run. Their manifests must all point to
that exact event CSV; otherwise the tools will reject the join.

After rebuilding those inputs, add the TimesFM features by event ID:

```bash
python scripts/join_timesfm_research_dataset.py \
  --research-dataset "data/processed/form4_research_datasets/BASE_RUN/event_research_dataset.csv" \
  --timesfm-features "data/processed/timesfm_features/TIMESFM_RUN/event_timesfm_features.csv"
```

The output under `data/processed/form4_timesfm_research/` retains the base
research columns and appends `timesfm_*` fields. It checks hashes, row counts,
event IDs, issuer identities, and event timestamps. Skipped forecasts remain
rows with an explicit status. The manifest preserves both source manifests and
states that forecast origins differ from the existing return-label origins.

## Research limitations

The time cutoff prevents post-event rows in the archived price CSV from entering
the model input. Historical adjusted closes can still be back-adjusted using
corporate actions known only after the event. The snapshot manifest records when
the price history was acquired, and the feature manifest calls out this caveat.
TimesFM's pretraining cutoff is also unknown. Therefore these outputs are
research features only; they are not a valid historical forecast track record.

The existing return labels start from the first close strictly after event
availability, while these initial forecasts start from the last close strictly
before it. Their horizons are not directly aligned. Before training a model,
create and test a target definition aligned with the forecast origin, handle
non-overlapping time splits and overlapping return windows, and compare against
simple price-history baselines. Preserve the existing SEC pipeline and keep all
model outputs away from optimizer/order interfaces until separately validated.


## Build origin-aligned realized returns

The event research table's `label_forward_return_*` fields use a start close after
the filing became available. Those labels do not use the same starting price as
the TimesFM forecasts. Build a separate matched evaluation artifact from the
TimesFM feature CSV and the exact same immutable price CSV and manifest:

```bash
python scripts/build_timesfm_actual_returns.py \
  --timesfm-features "$TIMESFM_RUN/event_timesfm_features.csv" \
  --prices "$PRICES" \
  --price-manifest "$PRICE_MANIFEST" \
  --primary-start-year 2010
```

For each successful forecast, the builder takes the TimesFM origin close and
measures the actual issuer close 1, 5, or 20 issuer sessions later. It verifies
the origin session, close time, origin price, source hashes, and manifest row
counts. Future market closes are used only as evaluation targets. The output
includes realized returns, forecast errors, directional agreement, SPY returns
when matching SPY sessions are present, and primary-sample MAE/RMSE/directional
accuracy. It reports a zero-return baseline for context and records status for
unavailable horizons. The artifact remains research-only.

Use chronological holdouts and account for overlapping horizons before drawing
conclusions. The pretrained model's cutoff and adjusted-price revision caveats
still prevent treating this as a clean historical simulation.
