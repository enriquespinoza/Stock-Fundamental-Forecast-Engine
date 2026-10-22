# Prospective TimesFM shadow evaluation

## Purpose

Measure whether TimesFM price-path features improve future-return predictions
when added to the same insider-trading and point-in-time financial feature set.
This is a research-only evaluation. Forecasts must not enter portfolio targets,
optimizer inputs, or execution interfaces during the test.

The historical TimesFM run is diagnostic only. Its checkpoint pretraining cutoff
is unknown, and the pilot cohort was selected from a 2026 S&P 500 snapshot. Do
not use that run to claim historical out-of-sample performance or broad-market
generalization.

## Paired model tracks

Freeze two horizon-specific models that share the same estimator, training rows,
preprocessing, labels, and hyperparameters:

1. **SEC baseline:** Form 4 transaction and owner features plus financial facts
   available by the event's historical availability time.
2. **SEC + TimesFM:** the exact same inputs plus TimesFM's 1-, 5-, and
   20-session forecast-return features available for that event.

Keep a zero-return forecast as a reference benchmark. Fit and freeze each model
before the prospective test begins. Record the code commit, model artifact hash,
feature-list hash, training-data manifest, estimator configuration, TimesFM
checkpoint revision/hash, and freeze timestamp. A change to any of these starts
a new experiment version; do not replace forecasts from the active version.

### Prospective calibration and freeze

Because the TimesFM checkpoint's pretraining cutoff is unknown, do not train the
augmented model on TimesFM forecasts generated for older historical events. Pin
the checkpoint to an immutable 40-character Hugging Face revision and start a
prospective calibration capture. The paired-model trainer accepts only events
available after that capture start, and only target closes observed before the
explicit training cutoff. It also requires each per-event TimesFM forecast
timestamp to precede that event's target close, so retrospective replays cannot
be passed off as prospective training inputs. Freeze the paired estimators after enough outcomes
have matured. The evaluation period begins after that freeze; calibration rows
are never counted as evaluation results.

This creates two separate clocks: a calibration period to fit both models, then
a holdout shadow period for the predeclared comparison. If the calibration
period has too few matured examples, keep collecting and do not begin the
evaluation clock.

## Prospective cohort and event capture

- Freeze the pilot symbols before the first scored event. Keep the cohort fixed
  for this experiment, retain delisted or acquired issuers in the record, and
  make no claims beyond this cohort. A broader study needs historical universe
  membership to address survivorship bias.
- Score only new events whose public-availability timestamp is after the freeze
  timestamp. Save each paired SEC-baseline and SEC-plus-TimesFM prediction before
  its outcome is known.
- Preserve one record per eligible Form 4 event for model scoring. Also record
  the issuer and forecast-origin session so repeated or overlapping outcomes
  can be clustered in statistical analysis.
- Use only SEC facts selected as of event availability and only market closes
  strictly before the event availability time. Future closes are labels only.
- Store the original forecast CSVs and manifests immutably. Build realized
  outcomes from a later market snapshot as closes mature. Verify the forecast
  origin session and close timestamp; compute returns from both origin and
  target closes in the same outcome snapshot, and record any adjusted-price
  revision relative to the forecast snapshot.

## Outcomes and analysis

- Define realized 1-, 5-, and 20-session returns from the same origin close used
  by the forecast. Compare each model's predictions on exactly the same matured
  event set.
- Primary horizons: 5 and 20 sessions. Treat 1 session as secondary.
- Primary comparison: paired change in MAE,
  `MAE(SEC + TimesFM) - MAE(SEC baseline)`. Negative values favor the augmented
  model. Also report RMSE, mean error, directional accuracy, and zero-return
  baseline metrics.
- Report results by issuer and transaction direction as diagnostics. Do not
  select symbols, horizons, or event subgroups based on this test and then
  present their same-period results as confirmatory.
- Use issuer-week block resampling or another predeclared clustered method for
  uncertainty intervals. Individual transaction rows and overlapping forward
  horizons are dependent observations.
- Continue for at least 12 months and until at least 300 distinct issuer/event
  availability clusters have matured at the 20-session horizon. If the count is
  not reached, extend the test; do not lower the threshold after seeing results.

## Readiness and decision gate

The prospective evaluation clock starts only after prospective calibration is
complete and both model artifacts, preprocessing, feature definitions, cohort,
TimesFM checkpoint revision, and code commit are frozen and hashed. The current
historical dataset does not satisfy this readiness gate: its TimesFM checkpoint
revision was not pinned and its feature rows were generated retrospectively.
Begin with a new pinned-checkpoint capture before training the pair.

## Commands

Install the optional model-training dependency with
python -m pip install -e ".[research]". Generate TimesFM features using a
full immutable checkpoint revision via the feature builder's
--checkpoint-revision option, then align actual returns to the forecast origins.
After the prospective calibration window has matured, train:

~~~sh
python scripts/train_shadow_models.py \
  --research-dataset PATH_TO_JOINED_TIMESFM_RESEARCH_CSV \
  --actual-returns PATH_TO_MATCHED_ACTUAL_RETURNS_CSV \
  --timesfm-feature-start-utc 2026-10-22T00:00:00Z \
  --training-cutoff-utc 2027-10-22T00:00:00Z \
  --code-revision FULL_40_CHARACTER_GIT_COMMIT
~~~

The emitted model directory contains the frozen estimator bundle, configuration,
input hashes, feature schema hash, and training summary. Use it to record paired
predictions from later events:

~~~sh
python scripts/build_shadow_predictions.py \
  --research-dataset PATH_TO_NEW_JOINED_TIMESFM_RESEARCH_CSV \
  --model-dir PATH_TO_FROZEN_MODEL_DIRECTORY
~~~

After outcomes mature, build matched return rows using the same TimesFM origin
and a newer price snapshot. Pass all prediction runs and outcome runs from the
same frozen model pair:

~~~sh
python scripts/score_shadow_predictions.py \
  --predictions PATH_TO_PREDICTION_RUN_1.csv PATH_TO_PREDICTION_RUN_2.csv \
  --actual-returns PATH_TO_OUTCOME_RUN_1.csv PATH_TO_OUTCOME_RUN_2.csv \
  --prices PATH_TO_ARCHIVED_PRICE_CSV \
  --price-manifest PATH_TO_PRICE_MANIFEST_JSON
~~~

Prediction output is append-only by run and contains paired model values without
outcome columns. The scorer verifies input hashes, model origins, event
availability, forecast creation time, and target-close maturity; it reports
paired loss changes and issuer-week cluster-bootstrap intervals. Pass all
prediction runs from the same frozen model pair to calculate cumulative
holdout metrics and check the fixed 12-month/300-cluster evaluation gate.

Keep the system in shadow mode through the full test. Consider a later research
review only if the augmented model improves the predeclared primary loss against
the SEC baseline, the paired uncertainty interval supports that improvement,
and results are not driven by one issuer or a small transaction subgroup. This
gate does not authorize portfolio or execution integration.
