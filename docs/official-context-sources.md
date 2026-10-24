# Official context source registry

## Purpose and boundary

The registry at `configs/sources/official_context_registry_v1.json` is a
pre-ingestion catalog for possible government-source context in issuer briefs.
It is not a downloader configuration yet. Each entry records the publisher,
source class, applicability, observation date, public-availability rule,
revision behavior, selection requirements, and interpretation limits.

The evidence package should distinguish:

1. **Company-reported primary evidence:** SEC filings and XBRL facts.
2. **Issuer-specific governance context:** for example PCAOB Form AP participant
   information. This is not a quality score.
3. **Industry and macro context:** BLS prices, BEA industry accounts, Treasury
   yields, EIA commodity series, and appropriately selected Fed/FRED data.
4. **System-wide context:** OFR indicators. These must never be displayed as
   company-specific warnings.
5. **Discovery-only catalogs:** Data.gov may locate data but should lead back to
   the originating agency before anything is ingested.

## Point-in-time requirements

Every future observation must carry its source observation period, actual public
release or publication time when available, acquisition timestamp, source URL,
query/series identifier, raw-response hash, units, and vintage or revision state.
For historical analysis, use the value available at the historical cutoff, not
the latest revised value. ALFRED is useful for historical vintages, but its
vintage date may not identify an exact intraday release time. If source timing is
unknown, use acquisition time as a conservative availability bound and mark the
observation accordingly.

Do not infer an issuer's economic exposure from its sector label alone. The
current universe sector is a starting filter. Before selecting a detailed BLS,
BEA, or EIA series, document the source-to-industry mapping, why it applies, its
frequency, revisions, and a human-readable interpretation limit. For example,
an oil benchmark is not Chevron's or Marathon's realized price or refining
margin.

## Pilot order

1. Keep SEC facts and accepted filings as the company-specific foundation.
2. Add a small market/macro pilot: Treasury 2-, 10-, and 30-year yields; BLS
   CPI-U headline (`CUUR0000SA0`) and broad Final Demand PPI (`WPUFD4`); then a
   limited BEA industry series after exact industry/table mapping.
3. Add EIA petroleum price series only for mapped energy issuers, after the
   specific crude/refined-product series and exposure rationale are declared.
4. Treat PCAOB auditor data as governance context, and OFR/FDIC as narrow
   issuer-family or system context as described in the registry.

This pilot should first measure source identity, coverage, release timing,
revision behavior, and join quality. It does not establish predictive value and
has no optimizer allocation or execution effect.

## Registry update policy

Do not edit or repurpose a source ID after observations have been captured.
Create a new versioned entry if publisher, series, interpretation, point-in-time
policy, or revision handling changes. Before enabling a source for ingestion,
verify its current official documentation and record the review date.
