# Universe expansion and survivorship controls

## Cross-sector ingestion pilot

The frozen cohort in [pilot_sp500_2026-10-07.csv](../configs/universe/pilot_sp500_2026-10-07.csv) contains 32 issuers across nine nonfinancial sectors, including the AAPL and MSFT plumbing baselines. The paired manifest records the selection snapshot, issuer CIK mapping source, and scope limits.

Use this cohort to expand SEC ingestion and validate issuer mapping, filing coverage, normalization, and market-price coverage. It is a **pipeline pilot only**. Its source is a current-member snapshot dated October 7, 2026; it does not include membership intervals or firms that left the index. The selected rows in the scanner snapshot identify their upstream as a Wikipedia-derived mirror. The SEC CIK map records current ticker associations, not historical security identity.

Do not use this cohort to calculate historical correlations, train forecasts, or make investable-performance claims. Doing so would introduce survivorship and selection bias.

## Historical research universe gate

Before historical return research, build or obtain a point-in-time security universe with:

- Membership effective dates for each security, including additions and removals.
- Companies that delisted, merged, went bankrupt, changed names, or changed tickers.
- Persistent security identifiers and a dated mapping between securities, tickers, and SEC issuer CIKs.
- Delisting proceeds/returns and corporate-action-adjusted total-return histories.
- Source, observation timestamp, license, and hash provenance for each membership and price snapshot.

A current S&P 500 list cannot reconstruct this history. S&P Dow Jones Indices describes historical S&P 500 constituent data in its [SPICE platform](https://www.spglobal.com/spdji/en/landing/topic/spice/). CRSP describes its U.S. stock databases as providing survivor-bias-free historical data and delisting information ([CRSP US Stock Databases](https://www.crsp.org/research/crsp-us-stock-databases-2025-layout/)). Confirm data access, coverage, and redistribution rights before adopting a source.

Use an index membership only while it is effective. For each historical decision date, first form the eligible set from information available on that date, then apply the filing-time, security-identity, financial-coverage, and price-coverage rules. Preserve delisted securities and their final outcomes in the evaluation sample.

## Research gate

After the historical universe and identifiers pass audit, predeclare chronological train/validation/test windows, purge overlapping forward-return horizons, retain an untouched final test period, and compare SEC features with price-only and zero-excess-return baselines. Report coverage and effective independent observations; repeated insider transactions and repeated financial values are not independent samples.

Until that gate passes, outputs remain data-plumbing and coverage validation. No correlation analysis or forecast training is authorized by this pilot configuration.
