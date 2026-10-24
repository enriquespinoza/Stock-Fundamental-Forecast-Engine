# Issuer evidence packages

## Purpose

The evidence package is a deterministic, point-in-time handoff from the existing
Python financial pipeline to future retrieval and report-writing components. It
preserves the facts, calculations, review prompts, and provenance already in an
issuer brief. It does not add new SEC retrieval, extract filing narrative, make
health judgments, or affect allocation or execution.

## Schema `issuer-evidence-package-v1`

The output is JSONL with one record per issuer. Each record contains:

- `issuer`: ticker and CIK.
- `as_of_utc`: the exact information cutoff inherited from the issuer brief.
- `research_status`: explicitly says no allocation effect.
- `source_brief`: path and SHA-256 of the exact input brief JSONL.
- `coverage`: metric availability counts from the source brief.
- `evidence_items`: typed items, each with a stable content-derived `evidence_id`.
- `text_retrieval_status`: indicates that document passages are not in this first version.
- `limitations`: constraints on interpretation.

`evidence_items.evidence_type` distinguishes:

| Type | Meaning | Provenance |
|---|---|---|
| `reported_fact` | One reported normalized fact for the latest or prior period, plus older facts cited by a review prompt | SEC form, accession, XBRL tag, period, availability time, unit, value, source URL |
| `derived_metric` | A ratio or comparable-period percentage change already calculated by the Python brief builder | Formula and source accession identifiers; as-of time |
| `rule_generated_review_prompt` | A transparent rule trigger for human review | Rule code, description, source evidence, and referenced evidence IDs |

Unavailable values are omitted as evidence items; coverage and the original brief
retain the missing-data state. Future facts are rejected if their availability
time exceeds `as_of_utc`. A review prompt remains explicitly unvalidated and is
not a verified adverse event, risk rating, or investment advice.

## Build

Build the issuer briefs first, then pass their JSONL and matching manifest:

```bash
python scripts/build_issuer_evidence_packages.py \
  --briefs data/processed/fundamental_briefs/<run>/issuer_fundamental_briefs.jsonl \
  --brief-manifest data/processed/fundamental_briefs/<run>/manifest.json
```

The run writes `issuer_evidence_packages.jsonl`, `summary.json`, and a hash
manifest under `data/processed/issuer_evidence_packages/`. This first pilot uses
the current 32-issuer brief run. Filing narrative chunks, earnings releases, and
other qualitative sources can be added in a later schema revision after their
source identity, timestamp, copyright, extraction accuracy, and point-in-time
behavior are defined and validated.

## RAG boundary

A future retrieval layer should index only source-backed evidence and filing
passages, filter by issuer and availability cutoff before retrieval, and return
source excerpts and IDs. An LLM may summarize that retrieved package, but it
must not invent facts or access optimizer/execution controls. The current
package builder is a data-format and provenance pilot, not an LLM feature.
