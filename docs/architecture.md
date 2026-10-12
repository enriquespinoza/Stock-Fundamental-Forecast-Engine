# Stock Fundamental Forecast Engine — proposed v1

Prepared October 12, 2026. Status: architecture and research specification; no model has been trained, no predictive edge has been established, and no existing repository has been modified.

## Purpose and responsibility

Build a standalone research engine that joins SEC EDGAR financial disclosures and insider transactions with market and macroeconomic data. Estimate forward stock total returns and benchmark-relative returns, quantify uncertainty, and explain the evidence available before an execution decision.

Suggested repository: `Stock-Fundamental-Forecast-Engine`.

The scanner identifies candidates. The optimizer proposes allocations. This engine evaluates the proposed holdings and current positions before execution, in tandem with TMF Predictive Analytics. If forecasts change expected returns or identify a risk gate, the orchestrator requests a new optimizer pass. Forecasts do not directly modify weights or authorize orders.

TMF remains a specialized Treasury/ETF model. Corporate accounting and corporate insider features do not apply to TMF in the same way as operating companies. Shared macro features must retain provenance so a later ensemble does not count the same evidence twice.

## Integration

1. Scanner supplies candidate identities and its frozen snapshot.
2. Optimizer supplies proposed allocations plus portfolio constraints and current positions.
3. Stock engine supplies forecasts, evidence, coverage, and risk flags for eligible corporate equities. TMF engine supplies its instrument-specific assessment.
4. Orchestrator reconciles both outputs. Changed inputs require re-optimization and a new frozen decision bundle; iterations are bounded and unresolved conflicts produce HOLD_FOR_REVIEW.
5. Execution consumes the final approved bundle and applies independent freshness, portfolio, broker, and human-approval controls. Existing signed, single-use approval challenges remain part of execution.
6. The separate Decision Review Engine records predictions and outcomes, evaluates drift and calibration, and proposes improvements for a future model version.

## Initial scope

Use 25–50 liquid US operating-company equities across several nonfinancial sectors to validate ingestion and timing. Treat this pilot as infrastructure validation, not evidence of a broad investment edge. Expand to a historical universe including delisted names before making generalized performance claims. Banks, insurers, REITs, foreign issuers, and ETFs need separate accounting mappings or explicit exclusions.

Primary research target: 63 trading-session total return and sector-relative total return. Secondary target: 21 sessions. Add 126 sessions only after the first targets are validated. Align forecasts with optimizer horizons; do not pass a 63-session forecast as an intraday signal. Analyze buys and sells separately, including whether a holding's expected return has weakened and the costs of turnover.

## Data sources and features

| Source | Proposed features | Controls |
| --- | --- | --- |
| 10-K / 10-Q and Company Facts XBRL | Revenue growth, margin change, operating cash flow, free cash flow, cash-flow-to-income ratio, accruals, leverage, coverage, share dilution | Preserve taxonomy, units, duration, accession, and version; handle year-to-date versus quarterly values and restatements |
| Forms 3 / 4 / 5; prioritize Form 4 XML | Purchase and sale value, distinct buyer count, insider role, change in ownership, clustered buying, derivative activity | Separate P/S transactions from awards, exercises, gifts, and tax withholding; preserve plan flags, footnotes, amendments, and ownership type |
| 8-K plus exhibits | Earnings releases, financing, management changes, disclosed material events | Track public availability separately from report period; text-derived claims must cite filing passages |
| Price, dividends, splits, benchmark and sector series | Momentum, volatility, drawdown, liquidity, valuation denominators, total-return labels | Keep raw and adjusted series separate; account for delistings and corporate actions |
| Treasury and other macro series | Rate changes, curve shape, regime interactions | Respect release timestamps and historical vintages; link shared features to TMF provenance |

Later research candidates: 13F holdings, Schedule 13D/G, buybacks, and filing-text changes. Institutional filings are delayed disclosures and must not be interpreted as contemporaneous trading activity. News, estimates, and earnings-call transcripts require separate coverage, licensing, and timestamp checks.

SEC Company Facts covers standard taxonomy facts at the entity level; it is not a complete replacement for filing documents, custom tags, segments, or Form 4 parsing. Use submissions metadata and EDGAR indexes to discover filings; retain original documents.

## Point-in-time data contract

Store issuer CIK and security identity separately; tickers change and issuers can have multiple share classes. Retain accession number, form, source URL, filing acceptance timestamp, observation/download timestamp, report period, feature timestamp, transformation version, and raw SHA-256.

For historical research, eligibility begins after documented public dissemination plus a declared processing delay. For live replay, it also cannot precede actual ingestion and processing. Map the eligible timestamp to the first tradable decision session. Use a conservative next-session convention for v1. Unknown historical availability means exclude or flag the observation, not invent an earlier timestamp.

Preserve the original filing and subsequent amendments as separate versions. A restated figure becomes usable only when the restatement becomes public. A current Company Facts response may contain later comparative disclosures: selecting the newest value for an old fiscal period would leak future information. As-of selection must filter by the filing/version actually available at the decision time.

Missing data is unknown, not zero. Apply explicit coverage gates. All preprocessing, imputation, normalization, feature selection, and calibration are fitted only on training windows.

## Research and models

Start with feature diagnostics: rank and linear associations with future sector-relative returns, effect size, sample size, sector/regime stability, and incremental contribution after momentum and market controls. Treat these as hypotheses rather than trading rules. Predeclare feature families and account for multiple comparisons.

Compare a zero-excess-return baseline and a price-only model with: financial-only; insider-only; financial plus insider; and combined price, fundamental, insider, and macro models. This isolates whether SEC features add information already captured in price or rates.

Begin with regularized regression and a simple probabilistic direction model. Evaluate a tree-based challenger only after the data pipeline and baseline pass. Numerical forecasts come from validated models. Optional language-model summaries explain cited documents and model outputs; they must not invent returns or probabilities.

Use chronological walk-forward train/validation/test windows. Keep all securities for a given test time block out of training, purge training labels whose forward windows overlap the test period, and set any embargo to the horizon/dependence structure. Lock a final untouched test period; do not repeatedly tune against it.

Evaluate error against baselines, rank information coefficient, probability calibration/Brier score, prediction-interval coverage, sector/regime stability, and net portfolio utility after realistic spreads, slippage, fees, liquidity limits, and turnover. Report clustered or block-bootstrap uncertainty and effective independent observations; daily repeats of quarterly facts are not independent accounting events.

## Forecast handoff v1

Each security/horizon record includes:

- `contract_version`, `symbol`, `security_id`, `issuer_cik`, `as_of_utc`, `decision_session`, `horizon_sessions`, `valid_until_utc`.
- `expected_total_return`, `expected_sector_excess_return`, `prob_positive_total_return`, `prob_outperform_sector`.
- `prediction_interval_lower`, `prediction_interval_upper`, `interval_nominal_coverage`, `interval_method`; probabilities and intervals can be null until calibrated.
- `model_version`, `training_cutoff`, `data_snapshot_sha256`, `coverage_status`, `data_age`, `risk_flags`, `evidence_accessions`, `source_urls`.
- `assessment`: FAVORABLE, NEUTRAL, UNFAVORABLE, or INSUFFICIENT_DATA. Initial assessments remain RESEARCH_ONLY until validation gates pass.

An assessment is evidence for a decision, not an order instruction. Stale, invalid, uncovered, or uncalibrated required outputs trigger a declared hold/review policy, not an assumed favorable forecast. The orchestrator checks horizon, timestamp, model approval, and snapshot compatibility.

## Repository layout

```text
src/stock_analyzer/
  ingestion/       # SEC client, indexes, filings, market and macro adapters
  normalization/   # identities, accounting periods, facts, ownership events
  features/        # fundamentals, insider activity, price, macro
  research/        # associations, ablations, stability reports
  models/          # baselines, fitted models, calibration
  validation/      # temporal splits, leakage checks, evaluation
  contracts/       # versioned forecast and provenance schemas
scripts/
  ingest_sec_filings.py
  build_point_in_time_features.py
  research_forward_returns.py
  train_walk_forward.py
  build_stock_forecast_handoff.py
tests/
configs/
docs/
reports/generated/
```

Store immutable raw documents and reproducible derived datasets outside tracked source when appropriate. Do not commit credentials or large downloaded archives.

## Delivery gates

1. **Data foundation:** ticker/CIK/security mapping, cached SEC requests, filing archive, Form 4 parser, point-in-time ledger. Test amendments, duplicate filings, public-versus-transaction timestamps, fiscal periods, and units.
2. **Feature research:** accounting and insider tables joined to verified total-return labels. Publish coverage and correlation reports with sample sizes and uncertainty. No production forecast claims.
3. **Forecast validation:** frozen baseline/challenger comparisons, ablations, untouched temporal test, calibrated uncertainty, and documented failures. Predeclare numeric acceptance thresholds before opening the final test.
4. **Integration:** versioned handoff and a dry simulation through optimizer, orchestrator, and SimulationBroker. Test stale data, model mismatch, conflicting engine views, missing evidence, and horizon mismatch.
5. **Prospective paper validation:** freeze the model and log forecasts before outcomes. Observe completed forecast horizons; simulation success alone does not validate predictive performance. Only promote after prospective results and operational checks support the intended use.

First implementation artifact: SEC ingestion plus the point-in-time filing ledger and deterministic parser fixtures. A model trained before these controls exist would not provide trustworthy evidence.

## SEC references

- [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces): submissions and standard XBRL Company Facts; no API key required for these public data APIs.
- [Developer Resources](https://www.sec.gov/about/developer-resources): archive/index access and fair-access guidance. Respect the published aggregate maximum of 10 requests/second; v1 should use a lower shared rate limit, identifying User-Agent, caching, retries, and backoff.
- [Insider Transactions and Forms 3, 4, 5](https://www.sec.gov/files/forms-3-4-5.pdf): transaction-code distinctions and reasons sales may occur. This bulletin is background; use current form instructions and XML schemas during implementation.

The architecture, horizons, model choices, and delivery gates above are proposed engineering/research decisions, not claims made by the SEC or established predictive findings.
