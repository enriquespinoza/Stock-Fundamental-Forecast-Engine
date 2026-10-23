# Reviewed 8-K section features

After manually reviewing each row in `item_reference_review.csv`, extract conservative Item 2.02 and Item 5.02 text indicators:

```sh
python scripts/extract_8k_section_features.py \
  --normalized "data/normalized/8k/<index-run>/normalized.json" \
  --review-csv "data/processed/8k_validation/<accession>/item_reference_review.csv"
```

The command refuses to proceed while any review row for the accession is `PENDING` or has an unknown status. It verifies hashes for the normalized index and archived source. The output JSONL has one row per recognized standard Item 2.02 or 5.02 section heading, with filing-level historical availability, amendment status, section offsets and excerpt, source/review hashes, and explicit text indicators. Item 9.01 is retained as context in the review artifact and does not become an event row.

For Item 2.02, the first-pass indicators cover press-release language, furnished wording, explicit fiscal-quarter/year phrases, and an Item 99.1 mention inside the section. For Item 5.02, they cover lettered subitems and departure, appointment, and compensation vocabulary. These are text indicators, not classifications of whether a person actually departed or was appointed. The extractor does not parse earnings values, infer sentiment, infer event direction, join returns, or produce forecasts. Each output is research-only and must be checked against source text before use in a study.

Run the extractor separately for each reviewed filing. A later cohort builder should enforce issuer and availability-time consistency and should not merge 8-K/A amendments into originals automatically.
