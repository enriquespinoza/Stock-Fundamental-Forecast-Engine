# Reviewed 8-K section features

After manually reviewing each row in `item_reference_review.csv`, extract conservative Item 2.02 and Item 5.02 text indicators:

```sh
python scripts/extract_8k_section_features.py \
  --normalized "data/normalized/8k/<index-run>/normalized.json" \
  --review-csv "data/processed/8k_validation/<accession>/item_reference_review.csv"
```

The command refuses to proceed while any review row for the accession is `PENDING` or has an unknown status. It verifies hashes for the normalized index and archived source. The v2 output JSONL has one row per recognized standard Item 2.02 or 5.02 section heading, with filing-level historical availability, amendment status, separate standard heading and body text, offsets, source/review hashes, and explicit text indicators. Item 9.01 is retained as context in the review artifact and does not become an event row.

For Item 2.02, body indicators cover press-release wording, literal `furnished` mentions, the explicit `not_deemed_filed_clause_present` clause, fiscal-period phrases, and Item 99.1 mentions. For Item 5.02, body indicators cover lettered subitems and departure, appointment, and compensation vocabulary. Field names explicitly mark these as textual mentions; they are not verified event labels. Standard heading words are kept in `standard_heading_text` and excluded from body indicators. Manual confirmation in the review CSV confirms the filing-text item reference only; it does not verify a substantive event. The extractor does not parse earnings values, infer sentiment or event direction, join returns, or produce forecasts. Each output is research-only and must be checked against source text before use in a study.

The extractor's small reviewed AAPL sample is useful for diagnosing section boundaries and feature behavior, but it is not large enough to establish predictive value.

Run the extractor separately for each reviewed filing. A later cohort builder should enforce issuer and availability-time consistency and should not merge 8-K/A amendments into originals automatically.
