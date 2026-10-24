# Repository guidance

## Project purpose

Build a research and reporting engine that turns public company filings into auditable, point-in-time company fundamentals and filing evidence. The engine may provide descriptive company-health context and review prompts to the broader investment research suite.

## Scope boundaries

- Keep issuer fundamentals, SEC filing events, insider disclosures, and company-specific evidence in this repository.
- Keep macroeconomic indicators, Treasury-rate analysis, and macro regime modeling in the separate TMF research project. Do not add external macro series or macro-derived features here unless the project owner explicitly changes this boundary.
- TimesFM is a separate, optional price-path research component. Its checkpoint pretraining cutoff is unknown; do not describe retrospective TimesFM results as historically out-of-sample.
- Research, model outputs, and fundamental briefs must not change optimizer allocations, create orders, or reach execution systems. Treat integration as descriptive risk/context only unless separately approved and validated.
- Do not describe associations, feature importance, or rule-based review prompts as causal effects, confirmed signals, investment advice, or validated risk ratings.

## Data and research rules

- Preserve source accession, form, issuer/security identity, filing/public timestamp, observation time, source URL, input hashes, and transformation/model version wherever applicable.
- Enforce point-in-time availability: features may use only information public and available by the declared event/as-of time. Keep filing date, report period, transaction date, acceptance time, and observation time distinct.
- Treat amendments and restatements as new information vintages. Do not rewrite the earlier information set with later facts.
- Keep units, taxonomy tags, periods, and filing context attached to financial facts. Be explicit about missing coverage and unsupported formats.
- Use a predeclared issuer cohort and preserve historical membership/security identity when making historical claims. Flag survivorship and look-ahead risks.
- Keep raw inputs immutable; write derived artifacts to versioned output directories with manifests and hashes.
- Never commit credentials, real contact details, local secrets, downloaded private data, or large generated datasets. Configure `SEC_USER_AGENT` locally with the operator's real contact email; keep placeholders in docs and examples.

## Implementation workflow

1. Inspect the current branch, working-tree status, relevant documentation, and existing tests before editing.
2. Confirm that a proposed change fits the scope above. For unclear or consequential changes to study criteria, data policy, model evaluation, or optimizer/execution integration, explain the issue and request direction before making that change.
3. Make the smallest coherent change. Keep optional research dependencies isolated from the core SEC pipeline.
4. Add or update tests for meaningful behavior, especially point-in-time guards, provenance/hash checks, malformed inputs, and leakage prevention.
5. Run focused tests first, then the full test suite when practical. Report exact commands and results; distinguish offline tests from live-source validation.
6. Review the diff for accidental data, secrets, generated files, or unrelated edits. Do not commit or publish unless the user asks.

## Reporting expectations

For each task, state what changed, why, tests run and results, known limitations, and whether live SEC/provider access was actually exercised. Do not imply that mocked or offline tests verify live ingestion.
