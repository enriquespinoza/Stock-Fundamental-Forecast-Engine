# SEC filing signals and future stock returns

## Study question

Which information in SEC filings is most strongly associated with subsequent stock returns, and which filing features add the most out-of-sample predictive value after accounting for market data and other SEC disclosures?

This protocol studies release-time information across filing types. Form 4 is one candidate signal family, not a requirement for every observation. The primary question is predictive and associational. A feature importance score or correlation is not a causal effect and does not show that a filing caused a price move.

## Two linked research tracks

1. **Historical SEC signal study.** Compare filing types and extracted feature groups using only information available by each event time. Use a point-in-time security universe, historical security identifiers, and chronological holdouts. This track does not require TimesFM.
2. **Prospective TimesFM add-on.** After the SEC-only feature definitions and historical evaluation are frozen, test whether TimesFM price-path features add value to a fixed SEC model. Because the checkpoint's pretraining cutoff is unknown, follow the separate calibration and holdout protocol in [the TimesFM shadow study](timesfm-shadow-evaluation.md). Do not let the TimesFM calendar delay or redefine the historical SEC study.

Report results for these tracks separately. Retrospective TimesFM features must not be represented as historically out-of-sample.

## Filing families and candidate features

### Initial priority

- **10-K and 10-Q:** reported financial levels and changes, growth rates, margins, cash conversion, leverage, liquidity, share count, and changes in risk-factor or management-discussion text. Keep reporting period, unit, taxonomy tag, source accession, and public availability timestamp.
- **8-K:** filing item categories and their exhibit context, with event families such as earnings or guidance, acquisitions, financing, leadership changes, auditor events, and material agreements. Preserve item numbers and separate multiple items in one filing. Do not treat every 8-K as the same event.
- **Forms 3, 4, and 5:** ownership changes, purchases, sales, owner role, transaction code, plan status, amendment status, and transaction amount where reported. Keep the existing Form 4 quality rules; unknown plan status remains unknown.
- **Schedules 13D and 13G:** beneficial-ownership changes, amendment indicators, ownership percentages, and stated purpose where structured or reliably parsed. Treat activist and passive filings as distinct categories.
- **13F filings:** institutional reported positions and changes in reported holdings, keyed by reporting manager, issuer security identifier, report period, and actual public filing time. A reported position is a delayed holding disclosure, not a contemporaneous institutional trade.

### Later or exploratory families

Proxy statements and other governance disclosures may add board, compensation, and shareholder-proposal features after the core data audit. Form 144 notices may be evaluated as a separate proposed-sale disclosure family; a notice must not be treated as proof that a sale was completed. Narrative embeddings or sentiment scores should remain exploratory until their extraction, versioning, and point-in-time behavior are tested.

EDGAR submissions metadata, XBRL company facts, filing archives, and the SEC's structured 13F and beneficial-ownership data can support these inputs. Full filing text and exhibits may require separate acquisition and parsing beyond Company Facts.

## Observation unit and release-time rules

The primary unit is a **issuer-security information event**: an accession and its material filing items, mapped to a persistent security identifier. Preserve accession-level rows for audit, then aggregate or cluster multiple filings from the same issuer that become available at nearly the same time. This prevents multiple exhibits or transaction rows from being counted as independent market events.

Use the filing's accepted/public timestamp plus the project's declared processing-delay convention as the historical availability proxy. Keep the actual system observation and feature-generation timestamps as separate fields. The filing date, report period, transaction date, and market reaction date are not substitutes for public availability.

For every event, select financial facts and market observations that were available by its timestamp. Freeze the feature snapshot before labels are constructed. Amendments and restatements create new versions; they do not rewrite the earlier information set.

Classify release timing as pre-market, during regular trading, after close, or non-trading day using the relevant exchange calendar and actual close timestamp. The primary outcome begins at the first executable session price after the information could have been processed. Also retain an event-close-to-next-close diagnostic where the close was already observable when the event arrived. Exclude or separately analyze records whose timestamps cannot be established.

## Outcomes and association analysis

Primary return horizons are 5 and 20 trading sessions after the predeclared start price; 1 session is secondary. Use both raw issuer return and benchmark-adjusted return over identical dates. Include price-only controls available at release time, such as prior returns and volatility. Record market-data source, adjustment policy, observation time, and snapshot hash.

For numeric filing features, report Pearson and Spearman associations as descriptive statistics. For cross-sectional release-date comparisons, compute rank information coefficients among issuers with comparable event-time windows; also report within-issuer associations separately. For filing categories, report matched event-study return distributions and uncertainty intervals. Do not present pooled event counts as independent observations.

Report category coverage, missingness, issuer counts, event counts, and unique issuer-date clusters alongside every estimate. Apply a predeclared multiple-testing correction, such as Benjamini-Hochberg false-discovery-rate control, to exploratory feature-level association screens. These screens nominate candidate signals; they do not confirm predictive value.

## Which features carry predictive value

Compare a fixed set of model tracks on the same chronological splits and matched observations:

1. Price-only baseline.
2. Each SEC filing family added separately to the price baseline.
3. SEC feature groups combined, with Form 4 retained as one group.
4. SEC features plus TimesFM, evaluated only under the separate prospective protocol.

Use a simple regularized benchmark first, then add more complex models only under a prespecified model-selection procedure. For feature weight, report standardized coefficient estimates as model-specific summaries, grouped ablation loss changes, and held-out grouped permutation importance. Recalculate importance across chronological folds and issuers. A single coefficient magnitude is not a universal rank when predictors are correlated; feature importance is predictive attribution, not causality.

Primary predictive metrics are paired MAE and rank information coefficient on an untouched chronological holdout. Also report RMSE, calibration or sign accuracy where appropriate, and results by release-time bucket, filing family, issuer, and market regime as diagnostics. Use issuer-week or calendar-block uncertainty estimates that preserve repeated events and common market shocks. Purge overlapping forward-return windows between training and evaluation. Freeze all feature dictionaries, model settings, hypotheses, exclusions, and decision rules before the final holdout is opened.

## Universe and survivorship controls

The existing 32-member pilot was selected from an October 2026 S&P 500 snapshot and has uneven filing depth. Use it for parser coverage and prospective pilot capture, not historical broad-market correlation or generalization claims.

Historical confirmation requires a point-in-time security universe with membership effective dates, persistent security identifiers, ticker/CIK history, delisted firms, mergers, bankruptcies, and terminal returns. If that source is unavailable, label historical analyses as selected-cohort exploratory results and limit claims to those issuers. Preserve issuers that later leave the index.

## Implementation sequence and gates

1. **Protocol and inventory:** record the frozen universe scope, supported filing families, release timestamp convention, horizons, feature families, and outcome definitions. Inventory historical coverage before expanding parsing.
2. **Filing parsers and fixtures:** extend the archived-document layer in this order: 8-K item/exhibit indexing; 10-K/10-Q narrative section and XBRL alignment; 13D/13G ownership changes; 13F manager positions. Preserve raw sources, hashes, availability times, parse status, and unsupported formats.
3. **Event and feature panel:** build one point-in-time issuer-security event table and audit duplicate timing, restatements, missingness, units, identifier continuity, and first actionable price.
4. **Descriptive screen:** estimate associations and release-time patterns on development data with cluster-aware uncertainty and the declared multiple-testing correction. Label discoveries exploratory.
5. **Historical predictive test:** proceed only after the historical universe and timestamps pass audit. Freeze features and models before evaluating the chronological holdout.
6. **TimesFM add-on:** keep calibration and prospective holdout separate. Compare the frozen SEC model with the same model plus TimesFM; do not mix TimesFM retrospective replays into its prospective evidence.
7. **Operational review:** any promising holdout result supports a research review only. A separate paper-trading and integration gate is required before a forecast can inform portfolio or execution systems.

The 2026 pilot cohort alone does not pass the historical-universe gate. Data acquisition for historical membership and issuer/security identity is therefore a prerequisite for broad historical claims. The current pipeline remains research-only and produces no orders.
