# 8-K item validation and first-pass features

The indexer locates visible Item references. The validation command independently re-reads the archived primary document, verifies source and normalized hashes, recounts references, and writes a CSV with nearby text excerpts for manual review.

```sh
python scripts/validate_8k_index.py \
  --normalized "data/normalized/8k/<run>/normalized.json"
```

The output folder contains `item_reference_review.csv`, `filing_features.json`, and `summary.json`. Reference review rows remain `PENDING` until a researcher checks the excerpts against the filing. A count match confirms that the archived source and index agree on regex matches; it does not establish that every mention is a substantive event heading.

The initial features are deliberately narrow:

- Item 2.02 presence, mention count, standard title phrase, furnished-language indicator, and an Exhibit 99.1 reference.
- Item 5.02 presence, mention count, and explicitly stated 5.02(a)-(f) subitem references.
- Item 9.01 presence and mention count as exhibit context only.

Every feature row preserves the issuer CIK, accession, form, filing date, historical availability timestamp, research eligibility, and source hash. No sentiment, event direction, dollar amount, or return prediction is inferred. Item 2.02 financial values require separate parsing and validation of the referenced earnings-release exhibit; Item 5.02 role and departure/appointment details require separate section-level extraction. Amendments remain identified as 8-K/A and are not silently consolidated.
