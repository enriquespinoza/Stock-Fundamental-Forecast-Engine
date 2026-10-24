# Codex project handoff

## Mission

Develop an auditable research engine for studying which public SEC filing information is associated with subsequent company returns and for producing grounded, descriptive company-level fundamental briefs. The engine is one independent component in the user's broader research suite; it is not an order or allocation system.

## Product boundary

The engine's intended contribution is **issuer-specific evidence and risk context**: financial trends, filing events, insider disclosures, source-backed explanations, coverage limitations, and review prompts. It may eventually supply a descriptive overlay to the optimizer, but it must not change portfolio weights or trigger trades without a separately specified, tested, and approved integration.

Macroeconomic indicators, interest-rate analysis, and macro regimes belong to the separate TMF project. Keep them out of this engine; the TMF workstream owns that context. Company filings can still contain management discussion of macro conditions, but these remain company statements sourced from SEC filings, not joined external macro indicators.

## Research questions

1. Which SEC filing families and extracted features are most associated with subsequent issuer returns at the time the information becomes public?
2. Do filing-derived feature groups add out-of-sample predictive value beyond a price-only baseline and beyond one another?
3. Which features provide useful, source-linked descriptive context for a company's fundamental review?
4. Separately, after a prospective shadow sample matures, does TimesFM add value to a fixed baseline? The model's pretraining cutoff is unknown, so historical TimesFM forecasts are not evidence of historical out-of-sample skill.

These are research questions. Correlation, model weights, grouped ablations, and feature importance do not establish causation or a universal ranking of investment quality.

## Data and feature scope

### Core and current candidate sources

- **SEC EDGAR:** 10-K, 10-Q, 8-K, Forms 3/4/5, and other filing families as each is explicitly added and validated.
- **XBRL Company Facts:** normalized reported facts with tags, units, period contexts, accession references, and point-in-time availability.
- **Market prices:** labels and price controls with immutable snapshots, provider/source metadata, adjustment policy, observation timestamp, and hashes.
- **Optional company/industry context:** use only when its approved source registry and provenance policy specify the issuer/sector applicability. EIA, PCAOB, and FDIC sources must remain limited to their relevant industries or governance context; Data.gov is for discovery unless a concrete dataset is separately approved.

### Filing-family priority

- **10-K/10-Q:** reported financial levels and changes, margins, cash conversion, liquidity/leverage measures, and carefully validated narrative features.
- **8-K:** item-level disclosure events. Prioritize Item 2.02 earnings/results disclosures and Item 5.02 leadership changes; preserve Item 9.01 as exhibit context rather than a standalone event. Separate heading text from body text and distinguish text mentions from verified event labels.
- **Forms 3/4/5:** ownership events with transaction code, reporting-owner role, plan status, amendment status, and quality flags. Unknown plan status remains unknown.
- **Other filing families:** 13D/13G, 13F, proxy statements, Form 144, auditor/governance filings, or narrative models are future candidates. Add them only with explicit point-in-time definitions and validation.

This list describes research priorities, not a claim that every family is already implemented or validated. Confirm the actual implementation on the active GitHub branch before relying on local copies.

## Point-in-time and evaluation criteria

- Use actual public/accepted timestamp plus a documented processing-delay convention to define historical availability; retain system observation and feature-generation timestamps separately.
- Select every input only if available by the event/as-of timestamp. Freeze feature snapshots before attaching future labels.
- Treat amendments and restatements as new vintages. Do not backfill later knowledge into earlier feature rows.
- Map events to persistent security identifiers and a predeclared cohort. For historical conclusions, preserve historical membership and security identity to limit survivorship bias.
- Use a chronological holdout, purge overlapping forward-return windows across the train/holdout boundary, and report issuer/date cluster counts. Report coverage, missingness, issuer concentration, and sample rules with every estimate.
- Compare at minimum a price-only baseline with filing-family additions on matched observations. Use grouped ablations and held-out importance as predictive attribution, not causality.
- Use 5- and 20-session returns as primary outcomes and 1-session as secondary unless a frozen protocol explicitly changes this. Align outcome start price to the forecast/event anchor and benchmark dates exactly.
- Keep descriptive reports and any future optimizer overlay separate from execution. Research results do not imply deployment readiness.

## Current status and handoff caveats

- The project has progressed through SEC filing ingestion/normalization, Form 4 event work, point-in-time financial joins, return labels, TimesFM research, 8-K indexing/section-feature validation, manual financial-fact review tools, SEC signal evaluation/diagnostics, issuer fundamental briefs, and issuer evidence packages. These components have different validation maturity; inspect the active branch and manifests before treating any output as current.
- Earlier pilot results were exploratory, limited to a 32-issuer cohort and affected by uneven filing/event coverage. They are not deployment evidence.
- A report from the latest offline setup says Python 3.12, editable installation, CLI help, and 15 tests passed. Treat this as a reported setup checkpoint, not as a substitute for rerunning the relevant and full test suites on the active branch.
- Live SEC ingestion has not been verified in that offline setup. It requires `SEC_USER_AGENT` containing an application name and the operator's real contact email. Never guess, solicit into tracked files, print, or commit the actual value. Keep it in a local environment variable or secret store.
- The scratch checkout available during handoff may be stale and have unrelated modified/untracked files. Do not publish its working tree wholesale. First open the correct GitHub repository and branch, inspect `git status`, and reconcile only the intended handoff files.
- The broader research suite has a Top-Level Investment Research Orchestrator concept: engines remain independently versioned research procedures, with immutable artifacts and explicit handoffs. Do not couple this repository directly to other engines as a shortcut.

## Recommended Codex first task

Review the active branch, repository guidance, project documentation, tests, and current worktree. Do not change code in this first pass. Return:

1. What is implemented and where, with the relevant paths.
2. Which tests pass on the active Python environment, and what they do not validate.
3. Data and model components that remain unverified or exploratory.
4. Any conflicts between current code/docs and this brief.
5. The smallest next implementation step that improves evidence quality without changing frozen research criteria or connecting to allocation/execution.

After that review, implement one narrow change at a time, test it, inspect the diff, and report the result. Do not commit, publish, or change optimizer/execution behavior unless explicitly asked.
