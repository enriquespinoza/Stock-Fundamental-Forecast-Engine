# Stock Fundamental Forecast Engine

Research engine for SEC filing, financial-statement, market, and macro features intended to estimate future stock returns. Works alongside TMF Predictive Analytics and informs the portfolio optimizer before the execution engine acts.

**Status: SEC normalization, point-in-time financial joins, and return-label tooling are implemented. Comparative filing-signal research and validated forecasts remain in progress. No established predictive edge or orders.**

## Implemented

- Identified SEC JSON client with conservative throttling, bounded retries, caching, and immutable SHA-256 snapshots.
- Submissions ingestion including additional historical pages declared by the SEC response.
- Raw Company Facts snapshots plus a narrow US-GAAP normalization layer, with filing-version provenance and as-of selection.
- Form 4 XML parsing for derivative/non-derivative transactions, holdings, joint reporting owners, plan flags, footnotes, and amendments.
- Version-preserving filing ledger: acceptance, observation, report period, amendment flag, and source URL.
- Separate historical and live availability cutoffs; unknown acceptance timestamps are excluded from as-of queries.
- Offline tests for availability, amendments, duplicates, malformed inputs, cache integrity, throttling, HTTP failures, and normalization commands.
- Point-in-time Form 4-to-financial joins with period provenance and future-fact exclusion.
- A separate market-price label stage for 1-, 5-, and 20-session issuer and SPY returns. It uses actual close timestamps and immutable input hashes; it does not train a model.
- A manifest-checked event-level research table that joins point-in-time financial facts and forward-return labels by event ID.
- An optional TimesFM 2.5 research feature builder with strict pre-event price cutoffs and a manifest-checked event-ID join; output is isolated from portfolio and execution systems.

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

To download market prices, install the optional provider and exchange-calendar packages with `python -m pip install -e ".[market-data]"`. To build optional TimesFM 2.5 features, install `python -m pip install -e ".[timesfm]"`; this adds the large PyTorch dependency and model checkpoint download.

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

The ledger uses acceptance metadata plus a declared processing delay as a conservative historical availability proxy. Live availability also respects actual download time. This is a timestamp ledger, not an exchange calendar: each research step must map eligible timestamps to the next actionable trading session. Do not use report dates or insider transaction dates as public availability timestamps.

Later comparative disclosures and restatements must not overwrite information available at earlier decision dates. The normalization command retains fact versions and selects facts only after their ledger availability cutoff. Missing acceptance times remain unknown; historical pages without those times need filing-header enrichment before use.

The current 32-issuer pilot was selected from a 2026 S&P 500 snapshot. Use it to validate ingestion, parsing, and prospective capture. It is not a historical point-in-time universe and cannot support broad historical correlation or generalization claims. See [universe expansion and survivorship controls](docs/universe-expansion.md).

## Build forward-return labels

See [market data and return labels](docs/market-data.md) for the download, label, and event-level dataset commands. Labels start from a close strictly after event availability, retain actual close times, and use immutable input hashes. The first AAPL run validates plumbing only.

## Research question and roadmap

The primary study asks which SEC filing families and extracted features are associated with subsequent returns, and which features add predictive value after controlling for market data and other SEC disclosures. Form 4 is one signal family among several.

See [SEC filing signals and future stock returns](docs/sec-filing-signal-study.md) for filing groups, release-time rules, hypotheses, model comparisons, feature-importance methods, and research gates. The historical SEC filing study and the prospective TimesFM add-on are separate tracks; the TimesFM-specific calibration and holdout calendar does not delay the SEC-only analysis.

The 2026 pilot cohort remains a pipeline and prospective-capture cohort. Broad historical claims require a survivor-free point-in-time security universe, stable issuer/security identifiers, delisting outcomes, and audited filing availability. Until those data pass review, historical pilot analyses remain exploratory.

Any future forecast interface to the orchestrator must publish versioned estimates, uncertainty, timestamps, and provenance. The analyzer does not place orders. A promising research result requires a separate paper-trading and operational validation gate before promotion.

See [architecture](docs/architecture.md) for component boundaries and [prospective TimesFM shadow evaluation](docs/timesfm-shadow-evaluation.md) for the model-specific add-on.

## SEC references

- [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [Developer resources and fair access](https://www.sec.gov/about/developer-resources)
- [Insider transaction background](https://www.sec.gov/files/forms-3-4-5.pdf)
