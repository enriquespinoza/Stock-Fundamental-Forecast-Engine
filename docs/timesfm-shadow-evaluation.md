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
- Store the original forecast CSVs and manifests immutably. Later scoring may
  use a newer price snapshot for realized outcomes, but must verify the event's
  origin session and disclose any adjusted-price revisions.

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

The prospective clock starts only after both model artifacts, preprocessing,
feature definitions, cohort, and code commit are frozen and hashed. The current
historical dataset does not satisfy this readiness gate because the final paired
SEC-only and SEC-plus-TimesFM prediction artifacts have not yet been created.

Keep the system in shadow mode through the full test. Consider a later research
review only if the augmented model improves the predeclared primary loss against
the SEC baseline, the paired uncertainty interval supports that improvement,
and results are not driven by one issuer or a small transaction subgroup. This
gate does not authorize portfolio or execution integration.
