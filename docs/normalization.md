# SEC normalization v1

This layer prepares documented observations for research. It neither estimates returns nor authorizes trades. Tests use synthetic fixtures; live SEC retrieval and a representative issuer audit are still required before treating coverage as validated.

## Financials

Use paths from `scripts/ingest_sec_filings.py` output and its manifest. From the repository root after editable installation:

```bash
python scripts/normalize_sec_data.py financials \
  --ledger "PATH_TO_FILING_LEDGER.jsonl" \
  --companyfacts "PATH_TO_COMPANYFACTS_SNAPSHOT.json" \
  --as-of "2026-10-12T21:00:00Z"
```

The output includes every retained normalized fact version, `selected_facts` as of the requested timestamp, derived free cash flow where inputs align, and coverage/quality `issues`. Use **selected facts**, not the full version history, as inputs to an as-of research query.

For live selection add `--mode live --facts-observed-at "ACTUAL_SNAPSHOT_OBSERVATION_TIMESTAMP"`. Reuse the Company Facts observation time from the ingestion manifest, not today's run time. Without that timestamp, live financial availability stays unknown. Timestamp strings must include a timezone.

Ten mapped metrics: revenue, net income, operating income, operating cash flow, capital expenditures, assets, liabilities, equity, cash, and common shares outstanding. Values are decimal strings, not floating-point approximations. Negative accounting values are permitted; missing metrics remain missing.

Each fact retains its original tag, unit, start/end dates, accession, filing date, acceptance/availability timestamps, snapshot checksum, and source URL. Period classification uses duration: quarter, half-year year-to-date, nine-month year-to-date, annual, other duration, or instant. Fiscal labels are retained as metadata and do not override actual period length.

As-of selection chooses the latest eligible filing version for each issuer/metric/unit/exact period. Within that version, ordered revenue tags resolve overlap. Alternatives are never added together. Repeated identical observations collapse; conflicting values within the same tag, filing, and period fail. Different revenue tag definitions may differ economically; the chosen tag remains visible for future issuer-specific audits.

Free cash flow is operating cash flow minus payments to acquire property, plant, and equipment. Both inputs must share issuer, exact period, unit, and accession. This is a declared research definition, not a universal accounting measure. Negative capital expenditure inputs are excluded from this derivation. Quarter-from-YTD subtraction, trailing-twelve-month calculations, debt aggregation, valuation, and ratios are deferred until compatible periods and reporting vintages can be verified. Annual or YTD values are not silently converted to quarters.

Unsupported currencies, forms, missing ledger entries, unavailable timestamps, mismatched filing metadata, and malformed periods/values are recorded as issues and excluded. The narrow layer covers whole-entity standard US-GAAP facts, not custom tags, segments, IFRS, banks, insurers, or REIT-specific measures. Duration labels are heuristics with tolerance for non-calendar fiscal reporting; retain exact dates for analysis.

## Form 4

Download one filing using its ledger accession and your configured real SEC User-Agent:

```bash
python scripts/normalize_sec_data.py form4 \
  --ledger "PATH_TO_FILING_LEDGER.jsonl" \
  --accession "ACCESSION_FROM_LEDGER" \
  --download
```

The command removes an SEC stylesheet/viewer directory from the primary-document path to request raw archive XML. An HTML viewer is rejected. A missing or non-XML primary document requires separate document discovery; this command does not search every archive attachment.

For already downloaded raw XML:

```bash
python scripts/normalize_sec_data.py form4 \
  --ledger "PATH_TO_FILING_LEDGER.jsonl" \
  --accession "ACCESSION_FROM_LEDGER" \
  --xml "PATH_TO_OWNERSHIP.xml" \
  --xml-observed-at "ACTUAL_XML_ACQUISITION_TIMESTAMP"
```

Output separates transactions and holdings. Each transaction keeps its security title, code, acquired/disposed direction, shares, price, reported gross value, ownership type, post-transaction holdings, footnotes, reporting-owner list, plan flag, and filing context. Derivatives also retain exercise price, underlying security/shares, and exercise/expiration dates. Decimal strings preserve precision; missing price is null and produces no gross value.

Joint-owner filings produce one row per transaction, with the owners attached as a list. They are not duplicated into multiple full-value rows. A filed plan flag is true/false when present and unknown when absent; its presence does not establish trading intent. Footnote text is preserved but not interpreted as a forecast signal.

P/S codes identify purchases/sales that may occur on an exchange or privately; they do **not** establish an open-market discretionary trade. `is_purchase_sale_candidate` is only a structural filter: non-derivative P/S, consistent direction, known amounts/price and historical availability, and no amendment reconciliation flag. It is not a buy/sell recommendation. Grants, exercises, tax withholding, gifts, and derivatives are kept separately. Unknown transaction codes are flagged.

Amendments remain separate and are withheld from automatic candidate aggregation. This version does not link or reconcile corrected transactions with original filings; raw transactions must not be blindly summed across amendments or jointly reported filings. Independent filings by different owners may also describe the same economic trade; cross-filing reconciliation remains required before aggregate insider features.

Historical availability comes from filing acceptance plus the ledger's declared delay. Live availability additionally respects actual XML acquisition. Insider transaction dates never establish public availability. DTD/entity declarations, issuer/form mismatches, invalid numeric amounts, and unresolved footnotes fail explicitly. The parser accepts UTF-8 ownership XML with or without namespaces and is not a full SEC XML-schema validator.

## Before correlation research

Audit representative real issuers and transaction types; enrich missing acceptance metadata; reconcile amendments and duplicate economic events; map identities and timestamps to exchange sessions; and join verified total-return labels. Current tests establish deterministic behavior against synthetic inputs, not predictive performance or complete SEC coverage.

Primary references: [current Form 4 and instructions](https://www.sec.gov/files/form4.pdf) and [SEC XBRL APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
