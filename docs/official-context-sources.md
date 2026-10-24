# Company, industry, and governance source registry

## Purpose and boundary

The active registry is `configs/sources/official_context_registry_v2.json`.
Macroeconomic indicator series are outside this engine's scope because TMF
Predictive Analytics handles rate and macro analysis. Registry v1 is retained
as historical documentation of the earlier proposal; it is superseded and must
not be used for new ingestion.

The v2 registry is a pre-ingestion catalog, not a downloader configuration.
Each entry records the publisher, source class, applicability, observation date,
public-availability rule, revision behavior, selection requirements, and
interpretation limits.

The evidence package should distinguish:

1. **Company-reported primary evidence:** SEC filings and XBRL facts.
2. **Issuer-specific governance context:** for example PCAOB Form AP participant
   information. This is not a quality score.
3. **Industry context:** narrowly mapped EIA commodity series may contextualize
   exposed industries. They are not macro indicators, issuer realized prices,
   margins, or warnings by themselves.
4. **Issuer-specific governance context:** PCAOB Form AP participant data and
   FDIC bank data where the issuer-to-regulated-entity mapping is verified.
5. **Discovery-only catalogs:** Data.gov may locate data but should lead back to
   the originating agency before anything is ingested.

## Point-in-time requirements

Every future observation must carry its source observation period, actual public
release or publication time when available, acquisition timestamp, source URL,
query/series identifier, raw-response hash, units, and revision state. If source
timing is unknown, use acquisition time as a conservative availability bound
and mark the observation accordingly. The stock engine does not ingest or
calculate from CPI, PPI, Treasury yields, FRED/ALFRED macro series, or OFR
system-wide indicators.

Do not infer an issuer's exposure from its sector label alone. Before selecting
an EIA series, document the source-to-industry mapping, why it applies, its
frequency, revisions, and a human-readable interpretation limit. For example,
an oil benchmark is not Chevron's or Marathon's realized price or refining
margin. Any company discussion of macro conditions found in an SEC filing may
be summarized as a company-reported statement with a filing citation; the engine
does not attach external macro indicator data to it.

## Pilot order

1. Keep SEC facts and accepted filings as the company-specific foundation.
2. Consider EIA petroleum price series only for mapped energy issuers, after
   the exact crude/refined-product series and exposure rationale are declared.
3. Treat PCAOB auditor data as governance context and FDIC data as bank-specific
   evidence only after issuer/subsidiary identity mapping is validated.

This pilot should first measure source identity, coverage, release timing,
revision behavior, and join quality. It does not establish predictive value and
has no optimizer allocation or execution effect. Rates and macroeconomic
indicators belong to the separate TMF engine.

## Registry update policy

Do not edit or repurpose a source ID after observations have been captured.
Create a new versioned registry if publisher, series, interpretation,
point-in-time policy, or revision handling changes. Before enabling a source for
ingestion, verify its current official documentation and record the review date.
