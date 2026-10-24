"""Create provenance-preserving evidence packages from issuer fundamental briefs.

These packages are the deterministic input boundary for a future search/RAG and
LLM reporting layer. They are not allocation inputs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .ledger import parse_timestamp, utc_text


SCHEMA_VERSION = "issuer-evidence-package-v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_jsonl(path: Path) -> tuple[list[dict], bytes]:
    raw = path.read_bytes()
    rows = []
    for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Expected a JSON object at {path}:{number}")
        rows.append(row)
    return rows, raw


def _stable_id(*parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fact_item(issuer_cik: str, symbol: str, family: str, metric: str,
               version: str, fact: dict, as_of: datetime) -> dict | None:
    if fact.get("status") != "AVAILABLE":
        return None
    available = fact.get("available_after_utc")
    if not available:
        raise ValueError(f"Available fact lacks availability timestamp: {symbol} {metric}")
    available_time = parse_timestamp(available)
    if available_time > as_of:
        raise ValueError(f"Future fact found in evidence package: {symbol} {metric}")
    accession = fact.get("accession_number")
    if not accession:
        raise ValueError(f"Available fact lacks accession: {symbol} {metric}")
    identity = _stable_id(issuer_cik, family, metric, accession,
                          fact.get("period_start"), fact.get("period_end"), fact.get("tag"))
    return {
        "evidence_id": identity,
        "evidence_type": "reported_fact",
        "issuer_symbol": symbol,
        "issuer_cik": issuer_cik,
        "metric": metric,
        "period_family": family,
        "observation": version,
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "period_start": fact.get("period_start"),
        "period_end": fact.get("period_end"),
        "availability": {
            "available_after_utc": utc_text(available_time),
            "as_of_utc": utc_text(as_of),
            "point_in_time_eligible": True,
        },
        "source": {
            "source_class": "SEC_FILING",
            "form": fact.get("form"),
            "accession_number": accession,
            "tag": fact.get("tag"),
            "url": fact.get("source_url"),
        },
    }


def _iter_metric_facts(brief: dict, as_of: datetime) -> list[dict]:
    result = []
    symbol, cik = brief["issuer_symbol"], brief["issuer_cik"]
    for family, section_name in (("10-K", "annual_10k"), ("10-Q", "quarterly_10q")):
        section = brief.get(section_name, {})
        for metric, details in section.get("metrics", {}).items():
            for version in ("latest", "prior"):
                item = _fact_item(cik, symbol, family, metric, version,
                                  details.get(version, {}), as_of)
                if item:
                    result.append(item)
        for ratio_name, ratio in section.get("period_aligned_ratios", {}).items():
            if ratio.get("value") is None:
                continue
            result.append({
                "evidence_id": _stable_id(cik, family, ratio_name, ratio.get("period_end"),
                                          ratio.get("numerator_accession"), ratio.get("denominator_accession")),
                "evidence_type": "derived_metric",
                "issuer_symbol": symbol,
                "issuer_cik": cik,
                "metric": ratio_name,
                "period_family": family,
                "observation": "latest_aligned_period",
                "value": ratio["value"],
                "unit": "ratio",
                "period_start": ratio.get("period_start"),
                "period_end": ratio.get("period_end"),
                "availability": {"as_of_utc": utc_text(as_of), "point_in_time_eligible": True},
                "calculation": {"formula": ratio_name, "numerator_accession": ratio.get("numerator_accession"),
                                "denominator_accession": ratio.get("denominator_accession")},
            })
        for metric, details in section.get("metrics", {}).items():
            if details.get("change_percent") is not None:
                latest = details.get("latest", {})
                prior = details.get("prior", {})
                if not latest.get("accession_number") or not prior.get("accession_number"):
                    continue
                result.append({
                    "evidence_id": _stable_id(cik, family, metric, "year_over_year_change",
                                              latest.get("period_end"), prior.get("period_end")),
                    "evidence_type": "derived_metric",
                    "issuer_symbol": symbol,
                    "issuer_cik": cik,
                    "metric": f"{metric}_year_over_year_change_percent",
                    "period_family": family,
                    "observation": "latest_vs_prior_comparable_period",
                    "value": details["change_percent"],
                    "unit": "percent",
                    "period_start": None,
                    "period_end": latest.get("period_end"),
                    "availability": {"as_of_utc": utc_text(as_of), "point_in_time_eligible": True},
                    "calculation": {"formula": "(latest / prior - 1) * 100",
                                    "latest_accession": latest["accession_number"],
                                    "prior_accession": prior["accession_number"]},
                })
    return result


def _signal_items(brief: dict, as_of: datetime) -> list[dict]:
    result = []
    for signal in brief.get("review_signals", []):
        evidence_refs = []
        for fact in signal.get("evidence", []):
            available = fact.get("available_after_utc")
            if available and parse_timestamp(available) > as_of:
                raise ValueError("Future evidence found in review signal")
            evidence_refs.append(_stable_id(brief["issuer_cik"], "10-K", fact.get("metric"),
                                            fact.get("accession_number"), fact.get("period_start"),
                                            fact.get("period_end"), fact.get("tag")))
        result.append({
            "evidence_id": _stable_id(brief["issuer_cik"], signal.get("code"), signal.get("periods"),
                                      signal.get("accessions")),
            "evidence_type": "rule_generated_review_prompt",
            "issuer_symbol": brief["issuer_symbol"],
            "issuer_cik": brief["issuer_cik"],
            "code": signal.get("code"),
            "status": signal.get("status"),
            "description": signal.get("description"),
            "periods": signal.get("periods", []),
            "evidence_refs": evidence_refs,
            "evidence": signal.get("evidence", []),
            "year_over_year_changes": signal.get("year_over_year_changes", []),
            "availability": {"as_of_utc": utc_text(as_of), "point_in_time_eligible": True},
            "source_class": "ENGINE_RULE",
            "interpretation_limit": "Unvalidated review prompt; not a risk rating, verified adverse event, or investment advice.",
        })
    return result


def build_evidence_packages(briefs_path: Path, output_root: Path, *,
                            brief_manifest_path: Path | None = None,
                            built_at_utc: str | None = None) -> dict:
    briefs_path, output_root = Path(briefs_path), Path(output_root)
    briefs, briefs_raw = _read_jsonl(briefs_path)
    if not briefs:
        raise ValueError("Brief JSONL contains no issuer records")
    packages = []
    seen = set()
    for brief in briefs:
        symbol, cik = brief.get("issuer_symbol"), brief.get("issuer_cik")
        if not symbol or not cik or symbol in seen:
            raise ValueError("Each issuer brief must have a unique symbol and a CIK")
        seen.add(symbol)
        as_of_text = brief.get("as_of_utc")
        if not as_of_text:
            raise ValueError(f"Issuer brief lacks as_of_utc: {symbol}")
        as_of = parse_timestamp(as_of_text)
        items = _iter_metric_facts(brief, as_of) + _signal_items(brief, as_of)
        existing_ids = {item["evidence_id"] for item in items}
        # Signals can cite older comparison facts that are not part of the brief's latest/prior slots.
        for signal in brief.get("review_signals", []):
            for fact in signal.get("evidence", []):
                adapted = {**fact, "status": "AVAILABLE",
                           "available_after_utc": fact.get("available_after_utc")}
                item = _fact_item(cik, symbol, "10-K", fact.get("metric", "unknown"),
                                  "signal_supporting_fact", adapted, as_of)
                if item and item["evidence_id"] not in existing_ids:
                    items.append(item)
                    existing_ids.add(item["evidence_id"])
        packages.append({
            "schema_version": SCHEMA_VERSION,
            "issuer": {"symbol": symbol, "cik": cik},
            "as_of_utc": utc_text(as_of),
            "research_status": "EVIDENCE_PACKAGE_RESEARCH_ONLY_NO_ALLOCATION_EFFECT",
            "source_brief": {"schema_version": brief.get("schema_version"),
                             "source_path": str(briefs_path), "source_sha256": _sha(briefs_raw)},
            "coverage": brief.get("data_quality", {}),
            "evidence_items": items,
            "text_retrieval_status": "NOT_INCLUDED; source filing text has not been ingested into this package",
            "limitations": [
                "Evidence contains only facts and prompts present in the source brief.",
                "A rule-generated prompt is not a verified event or risk rating.",
                "Company-specific XBRL tags and risks outside current mappings may be absent.",
            ],
        })
    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    out_dir = output_root / stamp.replace(":", "-")
    out_dir.mkdir(parents=True, exist_ok=False)
    package_path = out_dir / "issuer_evidence_packages.jsonl"
    with package_path.open("w", encoding="utf-8", newline="") as stream:
        for package in packages:
            stream.write(json.dumps(package, sort_keys=True, ensure_ascii=False) + "\n")
    manifest_path = Path(brief_manifest_path) if brief_manifest_path else None
    manifest_raw = manifest_path.read_bytes() if manifest_path else None
    summary = {
        "schema_version": "issuer-evidence-package-summary-v1",
        "package_count": len(packages),
        "issuer_symbols": sorted(seen),
        "evidence_item_count": sum(len(package["evidence_items"]) for package in packages),
        "evidence_type_counts": {
            kind: sum(item["evidence_type"] == kind for package in packages for item in package["evidence_items"])
            for kind in ("reported_fact", "derived_metric", "rule_generated_review_prompt")
        },
        "research_status": "EVIDENCE_PACKAGE_RESEARCH_ONLY_NO_ALLOCATION_EFFECT",
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "issuer-evidence-package-manifest-v1",
        "built_at_utc": stamp,
        "source_briefs": {"path": str(briefs_path), "sha256": _sha(briefs_raw), "row_count": len(briefs)},
        "source_brief_manifest": ({"path": str(manifest_path), "sha256": _sha(manifest_raw)}
                                   if manifest_path and manifest_raw is not None else None),
        "output": {"path": str(package_path), "sha256": _sha(package_path.read_bytes()),
                   "row_count": len(packages)},
        "summary_sha256": _sha(summary_path.read_bytes()),
        "research_status": "EVIDENCE_PACKAGE_RESEARCH_ONLY_NO_ALLOCATION_EFFECT",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")
    return {"output_dir": out_dir, "summary": summary, "manifest": manifest}
