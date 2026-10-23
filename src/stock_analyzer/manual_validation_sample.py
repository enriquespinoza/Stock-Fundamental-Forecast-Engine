"""Create deterministic, stratified manual-review queues for SEC extraction."""

import csv
import hashlib
import json
import random
from pathlib import Path

TARGET_8K_FAMILIES = {
    "earnings_or_financial_update",
    "director_or_officer_change",
}
FINANCIAL_METRICS = {
    "revenue", "net_income", "operating_income", "operating_cash_flow",
    "capital_expenditures", "assets", "liabilities", "equity", "cash",
    "shares_outstanding",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _year_band(value: str | None) -> str:
    if not value or len(value) < 4 or not value[:4].isdigit():
        return "unknown_year"
    year = int(value[:4])
    if year < 2010:
        return "pre_2010"
    lower = 2010 + ((year - 2010) // 5) * 5
    if lower >= 2025:
        return "2025_plus"
    return f"{lower}_{lower + 4}"


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _verified_8k_docs(root: Path) -> tuple[list[dict], list[dict]]:
    docs, inputs = {}, []
    for normalized_path in sorted(root.glob("*/normalized.json")):
        normalized_raw = normalized_path.read_bytes()
        manifest_path = normalized_path.parent / "manifest.json"
        manifest_raw = manifest_path.read_bytes()
        doc, manifest = json.loads(normalized_raw), json.loads(manifest_raw)
        if manifest.get("output", {}).get("sha256") != hashlib.sha256(normalized_raw).hexdigest():
            raise ValueError(f"Normalized 8-K hash mismatch: {normalized_path}")
        source_path = Path(manifest.get("source", {}).get("path", ""))
        if not source_path.is_file():
            raise ValueError(f"Archived 8-K source is missing: {source_path}")
        source_sha = _sha(source_path)
        if source_sha != manifest.get("source", {}).get("sha256") or source_sha != doc.get("source_sha256"):
            raise ValueError(f"Archived 8-K source hash mismatch: {source_path}")
        key = (doc["accession_number"], source_sha)
        candidate = {
            "doc": doc, "normalized_path": normalized_path, "normalized_sha256": hashlib.sha256(normalized_raw).hexdigest(),
            "manifest_path": manifest_path, "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "source_path": source_path, "source_sha256": source_sha,
        }
        previous = docs.get(key)
        if previous is None or normalized_path.parent.name > previous["normalized_path"].parent.name:
            docs[key] = candidate
    for record in docs.values():
        inputs.append({"path": str(record["normalized_path"]), "sha256": record["normalized_sha256"]})
    return list(docs.values()), sorted(inputs, key=lambda item: item["path"])


def _verified_financial_runs(root: Path) -> tuple[list[dict], list[dict]]:
    latest_by_issuer = {}
    for normalized_path in sorted(root.glob("*/normalized.json")):
        normalized_raw = normalized_path.read_bytes()
        manifest_path = normalized_path.parent / "manifest.json"
        manifest_raw = manifest_path.read_bytes()
        data, manifest = json.loads(normalized_raw), json.loads(manifest_raw)
        expected = manifest.get("normalized_sha256")
        if expected and expected != hashlib.sha256(normalized_raw).hexdigest():
            raise ValueError(f"Normalized financial hash mismatch: {normalized_path}")
        source = manifest.get("source", {})
        source_path = Path(source.get("path", ""))
        if source.get("path"):
            if not source_path.is_file():
                raise ValueError(f"Company Facts source snapshot is missing: {source_path}")
            if source.get("sha256") != _sha(source_path):
                raise ValueError(f"Company Facts source hash mismatch: {source_path}")
        issuer = data.get("issuer_cik")
        if not issuer:
            continue
        key = str(issuer).zfill(10)
        previous = latest_by_issuer.get(key)
        if previous is None or normalized_path.parent.name > previous["normalized_path"].parent.name:
            latest_by_issuer[key] = {
                "data": data, "normalized_path": normalized_path,
                "normalized_sha256": hashlib.sha256(normalized_raw).hexdigest(),
                "manifest_path": manifest_path,
                "companyfacts_path": str(source_path) if source_path else "",
                "companyfacts_sha256": source.get("sha256", ""),
            }
    runs = list(latest_by_issuer.values())
    inputs = [{"path": str(run["normalized_path"]), "sha256": run["normalized_sha256"]} for run in runs]
    return runs, sorted(inputs, key=lambda item: item["path"])


def _sample_strata(records: list[dict], stratum_fields: tuple[str, ...], per_stratum: int,
                   seed: int, *, balance_field: str | None = None) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    strata = {}
    for record in records:
        key = tuple(record.get(field, "") for field in stratum_fields)
        strata.setdefault(key, []).append(record)
    selected, counts = [], {}
    for key in sorted(strata):
        pool = list(strata[key])
        rng.shuffle(pool)
        chosen = []
        if balance_field:
            seen = set()
            for record in pool:
                value = record.get(balance_field, "")
                if value not in seen:
                    chosen.append(record)
                    seen.add(value)
                    if len(chosen) == per_stratum:
                        break
        if len(chosen) < per_stratum:
            chosen_ids = {id(record) for record in chosen}
            remaining = [record for record in pool if id(record) not in chosen_ids]
            chosen.extend(remaining[:per_stratum - len(chosen)])
        selected.extend(chosen)
        counts["|".join(map(str, key))] = {"population": len(pool), "selected": len(chosen)}
    return selected, counts


def build_manual_validation_sample(eight_k_root: Path, financials_root: Path, output_dir: Path,
                                   *, seed: int = 20261023, eight_k_per_stratum: int = 2,
                                   financial_facts_per_stratum: int = 1) -> dict:
    """Write filing-level 8-K and fact-level 10-Q/10-K review queues.

    Strata are 8-K item-family/form/year-band/presence and financial
    metric/form/year-band. Sampling is deterministic for fixed inputs and seed.
    """
    if eight_k_per_stratum < 1 or financial_facts_per_stratum < 1:
        raise ValueError("Per-stratum sample sizes must be positive")
    eight_k_root, financials_root, output_dir = map(Path, (eight_k_root, financials_root, output_dir))
    eight_k_docs, eight_k_inputs = _verified_8k_docs(eight_k_root)
    financial_runs, financial_inputs = _verified_financial_runs(financials_root)

    filing_units = {}
    for record in eight_k_docs:
        doc = record["doc"]
        items = doc.get("items", [])
        target_families = sorted({item.get("family") for item in items} & TARGET_8K_FAMILIES)
        strata_families = target_families or ["NO_TARGET_ITEM"]
        for family in strata_families:
            unit = {
                "accession_number": doc["accession_number"], "issuer_cik": doc["issuer_cik"],
                "form": doc["form"], "filing_date": doc.get("filing_date", ""),
                "filing_year_band": _year_band(doc.get("filing_date")),
                "historical_available_after_utc": doc.get("historical_available_after_utc", ""),
                "historical_research_eligible": str(bool(doc.get("historical_research_eligible"))).lower(),
                "sample_family": family, "target_item_families_present": ";".join(target_families),
                "indexed_items": ";".join(sorted(item.get("item", "") for item in items)),
                "normalized_path": str(record["normalized_path"]),
                "normalized_sha256": record["normalized_sha256"],
                "index_manifest_path": str(record["manifest_path"]),
                "index_manifest_sha256": record["manifest_sha256"],
                "source_path": str(record["source_path"]), "source_sha256": record["source_sha256"],
                "sampling_stratum": f"{family}|{doc['form']}|{_year_band(doc.get('filing_date'))}",
                "manual_review_status": "PENDING", "verified_item_codes": "",
                "verified_event_description": "", "review_notes": "",
            }
            filing_units[(doc["accession_number"], family)] = unit
    sampled_8k, eight_k_strata = _sample_strata(
        list(filing_units.values()), ("sample_family", "form", "filing_year_band"),
        eight_k_per_stratum, seed,
    )

    fact_units = []
    financial_run_inputs = []
    for run in financial_runs:
        data = run["data"]
        financial_run_inputs.append({"path": str(run["normalized_path"]), "sha256": run["normalized_sha256"]})
        for fact in data.get("facts", []):
            if fact.get("form") not in {"10-Q", "10-Q/A", "10-K", "10-K/A"}:
                continue
            metric = fact.get("metric")
            if metric not in FINANCIAL_METRICS:
                continue
            year = fact.get("filed_date") or fact.get("filing_date") or ""
            band = _year_band(year)
            unit = {
                "issuer_cik": fact.get("issuer_cik", data.get("issuer_cik", "")),
                "accession_number": fact.get("accession_number", ""), "form": fact.get("form", ""),
                "filing_date": fact.get("filed_date", ""), "filing_year_band": band,
                "historical_available_after_utc": fact.get("historical_available_after_utc", ""),
                "metric": metric, "tag": fact.get("tag", ""), "tag_preference": fact.get("tag_preference", ""),
                "value": fact.get("value", ""), "unit": fact.get("unit", ""),
                "period_kind": fact.get("period_kind", ""), "period_start": fact.get("period_start", ""),
                "period_end": fact.get("period_end", ""), "fiscal_year": fact.get("fiscal_year", ""),
                "fiscal_period": fact.get("fiscal_period", ""), "source_url": fact.get("source_url", ""),
                "companyfacts_snapshot_path": run["companyfacts_path"],
                "companyfacts_snapshot_sha256": run["companyfacts_sha256"],
                "normalized_financials_path": str(run["normalized_path"]),
                "normalized_financials_sha256": run["normalized_sha256"],
                "sampling_stratum": f"{metric}|{fact.get('form')}|{band}|{fact.get('period_kind', '')}",
                "manual_review_status": "PENDING", "observed_tag": "", "observed_value": "",
                "observed_unit": "", "observed_period_start": "", "observed_period_end": "",
                "review_notes": "",
            }
            fact_units.append(unit)
    sampled_facts, financial_strata = _sample_strata(
        fact_units, ("metric", "form", "filing_year_band", "period_kind"), financial_facts_per_stratum,
        seed + 1, balance_field="issuer_cik",
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    eight_k_path = output_dir / "8k_filing_review_sample.csv"
    facts_path = output_dir / "financial_fact_review_sample.csv"
    _write_csv(eight_k_path, sampled_8k, list(sampled_8k[0]) if sampled_8k else [
        "accession_number", "issuer_cik", "form", "filing_date", "sample_family", "manual_review_status",
    ])
    _write_csv(facts_path, sampled_facts, list(sampled_facts[0]) if sampled_facts else [
        "issuer_cik", "accession_number", "form", "metric", "tag", "value", "manual_review_status",
    ])
    manifest = {
        "schema_version": "sec-manual-validation-sample-v1", "seed": seed,
        "sampling_policy": {
            "eight_k": "random sample within target item family, form, and five-year filing band; filings without target items form a negative-coverage stratum",
            "financial_facts": "random sample within metric, form, and five-year filing band; prefer distinct issuers within each stratum",
            "selection_is_for_manual_extraction_validation_not_return_analysis": True,
            "8k_per_stratum": eight_k_per_stratum,
            "financial_facts_per_stratum": financial_facts_per_stratum,
        },
        "population_counts": {"8k_indexed_documents": len(eight_k_docs),
                               "financial_issuer_runs_latest_per_issuer": len(financial_runs),
                               "financial_fact_candidates": len(fact_units)},
        "sample_counts": {"8k_filing_units": len(sampled_8k), "financial_fact_rows": len(sampled_facts)},
        "8k_strata": eight_k_strata, "financial_fact_strata": financial_strata,
        "input_normalized_8k_files": eight_k_inputs,
        "input_latest_financial_runs": sorted(financial_run_inputs, key=lambda item: item["path"]),
        "outputs": {
            "8k_filing_review_sample": {"path": str(eight_k_path), "sha256": _sha(eight_k_path)},
            "financial_fact_review_sample": {"path": str(facts_path), "sha256": _sha(facts_path)},
        },
        "review_status": "PENDING_MANUAL_ANNOTATION", "research_only": True,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["manifest_sha256"] = _sha(manifest_path)
    (output_dir / "summary.json").write_text(json.dumps({
        "schema_version": "sec-manual-validation-sample-summary-v1",
        "sample_counts": manifest["sample_counts"],
        "population_counts": manifest["population_counts"],
        "8k_strata": eight_k_strata, "financial_fact_strata": financial_strata,
        "manifest_sha256": manifest["manifest_sha256"],
        "research_only": True,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
