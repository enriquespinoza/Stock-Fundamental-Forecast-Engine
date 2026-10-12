# Stock Fundamental Forecast Engine

Research engine for financial-statement, insider-transaction, market, and macro features intended to estimate future stock returns. Works alongside TMF Predictive Analytics and informs the portfolio optimizer before the execution engine acts.

**Status: Phase 1 ingestion and normalization. No forecasts, trading recommendations, or established predictive edge yet.**

## Implemented

- Identified SEC JSON client with conservative throttling, bounded retries, caching, and immutable SHA-256 snapshots.
- Submissions ingestion including additional historical pages declared by the SEC response.
- Raw Company Facts snapshots plus a narrow US-GAAP normalization layer, with filing-version provenance and as-of selection.
- Form 4 XML parsing for derivative/non-derivative transactions, holdings, joint reporting owners, plan flags, footnotes, and amendments.
- Version-preserving filing ledger: acceptance, observation, report period, amendment flag, and source URL.
- Separate historical and live availability cutoffs; unknown acceptance timestamps are excluded from as-of queries.
- Offline tests for availability, amendments, duplicates, malformed inputs, cache integrity, throttling, HTTP failures, and normalization commands.

## Setup and tests

Python 3.11 or newer. From the repository root:

```bash
python -m venv .venv
# Git Bash on Windows:
source .venv/Scripts/activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

The implementation uses the Python standard library. The editable installation requires setuptools as its build backend.

## Ingest one issuer

Set an identifying SEC User-Agent with your real contact email; do not use the example address below in a live request.

```bash
export SEC_USER_AGENT='StockFundamentalForecastEngine your-real-email@your-domain.com'
python scripts/ingest_sec_filings.py --cik 320193
```

CIK 320193 identifies Apple. Use `--refresh` for fresh requests; otherwise cached responses retain their original observation timestamps. Use `--recent-only` only when deliberately excluding historical pages.

Outputs under ignored `data/`:

- `raw/sec/<URL hash>/<content SHA-256>.json`: immutable SEC response bytes.
- `raw/sec/<URL hash>/observation-*.json`: source and observation history.
- `processed/<CIK>/<run timestamp>/filing_ledger.jsonl` and `manifest.json`.

The client is sequential and uses two requests per second by default. Coordinate rate limits across processes; SEC's aggregate ceiling is ten requests per second. Avoid simultaneous clients until a shared limiter exists. Cached corruption fails rather than silently accepting altered data.

## Timing rules and limits

The ledger uses acceptance metadata plus a declared processing delay as a conservative historical availability proxy. Live availability also respects actual download time. This is a timestamp ledger, not an exchange calendar: a later step must map eligible timestamps to the next trading session. Do not use report dates or insider transaction dates as public availability timestamps.

Later comparative disclosures and restatements must not overwrite information available at earlier decision dates. The normalization command retains fact versions and selects facts only after their ledger availability cutoff. It does not yet construct a market-session feature panel. Missing acceptance times remain unknown; historical pages without those times need filing-header enrichment before use.

Individual raw Form 4 XML can now be downloaded and cached through the normalization command. Bulk document discovery, security/ticker history, session calendars, total-return labels, and models are **not implemented**. Financial mappings are limited to ten US-GAAP metrics; custom tags, IFRS, segments, and industry-specific accounting remain uncovered. Refresh observations do not reconstruct what our system downloaded before it existed.

## Normalize financial facts and Form 4

See [normalization guide](docs/normalization.md) for commands, output fields, and limitations. Start with an ingestion run's ledger and the Company Facts raw path listed in its manifest. The normalization command creates a new run directory containing `normalized.json` and `manifest.json`.

## Integration and research roadmap

1. Finish the point-in-time data foundation: archive filing documents, enrich acceptance metadata, parse Form 4 XML, normalize accounting periods and security identities.
2. Join features to verified 21- and 63-session total-return labels; assess correlations, stability, and coverage.
3. Compare price-only baselines against SEC-feature models in chronological walk-forward tests.
4. Publish versioned forecasts, calibrated uncertainty, and cited evidence to the orchestrator. Changed forecasts can trigger re-optimization; the analyzer never places orders.
5. Run dry integration with SimulationBroker, then prospective paper validation over completed forecast horizons before promotion.

See [architecture](docs/architecture.md) for contracts, scope, and validation gates. Model changes remain experimental until validated and pinned to a specific version. The Decision Review Engine measures predictions against outcomes without silently changing a frozen model.

## SEC references

- [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [Developer resources and fair access](https://www.sec.gov/about/developer-resources)
- [Insider transaction background](https://www.sec.gov/files/forms-3-4-5.pdf)

