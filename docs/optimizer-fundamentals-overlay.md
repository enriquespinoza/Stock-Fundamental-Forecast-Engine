# SEC fundamentals overlay for optimizer research

## Decision

Build the SEC pipeline as a **descriptive issuer-analysis overlay** next to the portfolio optimizer. It may summarize reported fundamentals and raise transparent review flags. It must not change candidate eligibility, target weights, or orders in this phase.

The current return-model results do not establish that SEC features improve forecasts. The overlay is a separate research product with a different purpose: help a person understand a stock already under consideration and identify filing details worth reviewing.

## Three layers of output

Keep these outputs visibly distinct:

1. **Reported facts** — value, unit, period, filing form/accession, filed/available time, and source link.
2. **Descriptive calculations** — margins, leverage/liquidity measures, cash-flow conversion, and changes versus comparable periods. Each calculation carries its formula, inputs, and missing-data state.
3. **Review flags** — rule-triggered prompts such as "operating cash flow below net income for two annual periods" or "current liabilities exceed current assets." A flag is not an investment recommendation or a verified adverse event.

Avoid a single opaque "healthy/unhealthy" grade. Use explicit statuses such as `FLAG_FOR_REVIEW`, `NO_RULE_TRIGGERED`, and `INSUFFICIENT_DATA`. Never treat missing facts as zero.

## First report version

Create one as-of issuer report per symbol, using only facts whose filing availability timestamp is no later than the report's as-of timestamp. Include:

- Filing freshness and coverage by metric, period, and form family.
- Annual 10-K and single-quarter 10-Q values separately, with source accessions and tags.
- Revenue, operating income, net income, operating cash flow, capital expenditures, free cash flow, assets, liabilities, equity, cash, and shares outstanding when available.
- Comparisons only between compatible periods (same fiscal period/year-over-year or sequential quarter) and compatible units.
- The exact trigger, threshold, evidence, and source for every review flag.
- A prominent list of unavailable or stale inputs.

Initial rule candidates should be prompts for human review, not conclusions: repeated negative operating cash flow, free-cash-flow deterioration, shrinking cash alongside rising liabilities, declining operating margin, material share-count growth, and filing/XBRL coverage gaps. Thresholds should be sector-aware or expressed as changes from the issuer's own history. Do not label any of these as default buy/sell rules.

Deeper filing context can be added in later stages: 10-K/10-Q MD&A and risk-factor changes, auditor opinion and internal-control disclosures, debt maturities, and material 8-K items. Text-derived cues need their own source excerpts and manual validation. A textual mention must not be presented as a verified event.

### Build the first briefs

After pulling the feature, run from the repository root with the research environment active:

```bash
python scripts/build_issuer_fundamental_briefs.py \
  --financials-root data/normalized/financials \
  --universe configs/universe/pilot_sp500_2026-10-07.csv \
  --as-of "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
```

The run writes an issuer JSONL brief, a CSV of review prompts, summary, and hash manifest under `data/processed/fundamental_briefs/`. Every prompt carries fact values, units, periods, XBRL tags, filing accessions, availability timestamps, and source links; annual revenue prompts also include the two year-over-year percentage changes. The first prompts are deliberately narrow: two consecutive annual periods of negative operating cash flow, free cash flow, or operating income; two annual revenue declines; and liabilities above assets at the same annual balance-sheet date. These are **unvalidated review rules**, with no severity ranking or optimizer effect. A revenue decline by itself does not establish financial distress, particularly for commodity-sensitive businesses; inspect profit, cash-flow, price/volume, and management discussion context.

## Optimizer boundary

For the first version, produce a sidecar company brief that the user can inspect beside optimizer candidates. The report is read-only and has no import into the allocation objective, constraints, scanner ranking, or execution handoff. It cannot veto or boost a stock automatically.

If later research proposes using a flag in optimizer decisions, treat that as a new model/policy change: predeclare the rule, test it out of time on a point-in-time company universe, compare with the unchanged optimizer, document the effect on risk and turnover, and require explicit human approval before any paper-trading integration.

## Validation and promotion gates

- Verify each reported value against Company Facts and a stratified sample against the original filing.
- Check period alignment, amendments, units, stale facts, and company-specific tags.
- Measure coverage and false-positive rates for every rule by sector, issuer, and year.
- Keep descriptive accuracy evaluation separate from return-prediction evaluation.
- Preserve immutable source manifests, hashes, as-of times, and feature/rule versions.
- Leave all outputs research-only until a separate review approves a promotion path.

The SEC's EDGAR APIs provide company submissions and extracted XBRL data. The API is a retrieval layer; the filed report remains the audit source for context and company-specific disclosure. References: [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [EDGAR XBRL Guide](https://www.sec.gov/files/edgar/filer-information/specifications/xbrl-guide.pdf).
