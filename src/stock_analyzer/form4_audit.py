"""Audit normalized Form 4 artifacts and summarize insider activity."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORM4_FORMS = {"4", "4/A"}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _source_path(value: str, base: Path) -> Path:
    # Manifests created on Windows contain backslashes; normalize them so audits
    # also work when a copied data directory is audited on another platform.
    path = Path(value.replace("\\", "/"))
    return path if path.is_absolute() else base / path


def _owner_roles(owners):
    roles = []
    for owner in owners or []:
        assigned = False
        for key, label in (("is_director", "director"), ("is_officer", "officer"),
                           ("is_ten_percent_owner", "ten_percent_owner"), ("is_other", "other")):
            if owner.get(key) is True:
                roles.append(label)
                assigned = True
        if not assigned:
            roles.append("unspecified")
    return roles


def audit_form4_history(ledger_path: Path, normalized_root: Path, *,
                        batch_summary_path: Path | None = None,
                        base_dir: Path | None = None) -> dict:
    """Check artifact hashes/coverage and summarize filing and transaction fields.

    Source snapshots are re-hashed when the manifest's source path is available.
    An unavailable snapshot is reported separately from a hash mismatch.
    """
    ledger_path, normalized_root = Path(ledger_path), Path(normalized_root)
    base_dir = Path(base_dir or Path.cwd())
    ledger_raw = ledger_path.read_bytes()
    ledger_rows = [json.loads(line) for line in ledger_raw.decode("utf-8").splitlines() if line.strip()]
    form4_rows = [row for row in ledger_rows if row.get("form") in FORM4_FORMS]
    ledger_by_accession = {row["accession_number"]: row for row in form4_rows}
    if len(ledger_by_accession) != len(form4_rows):
        raise ValueError("Ledger has duplicate Form 4 accession numbers")

    artifacts = []
    integrity_issues = []
    for manifest_path in sorted(normalized_root.glob("form4/*/manifest.json")):
        try:
            manifest = _read_json(manifest_path)
            normalized_path = manifest_path.parent / "normalized.json"
            if manifest.get("command") != "form4" or not normalized_path.is_file():
                integrity_issues.append({"path": str(manifest_path), "reason": "INVALID_FORM4_ARTIFACT"})
                continue
            normalized_raw = normalized_path.read_bytes()
            digest = _sha256(normalized_raw)
            normalized = json.loads(normalized_raw.decode("utf-8"))
            filing = normalized.get("filing", {})
            accession = filing.get("accession_number")
            source = manifest.get("source", {})
            declared_source_hash = source.get("sha256")
            checks = {
                "normalized_hash_valid": bool(SHA256_RE.fullmatch(manifest.get("normalized_sha256", "")))
                    and manifest.get("normalized_sha256") == digest,
                "source_hash_well_formed": bool(SHA256_RE.fullmatch(declared_source_hash or "")),
                "source_hash_matches_document": declared_source_hash == filing.get("raw_sha256"),
                "ledger_hash_matches": manifest.get("ledger_sha256") == _sha256(ledger_raw),
            }
            raw_verified = None
            source_path = source.get("path")
            if source_path and checks["source_hash_well_formed"]:
                raw_path = _source_path(source_path, base_dir)
                if raw_path.is_file():
                    raw_verified = _sha256(raw_path.read_bytes()) == declared_source_hash
                else:
                    raw_verified = False
            checks["source_file_verified"] = raw_verified
            for check, passed in checks.items():
                if passed is False:
                    integrity_issues.append({"path": str(manifest_path), "accession_number": accession,
                                             "reason": check.upper()})
            artifacts.append({"accession_number": accession, "path": str(normalized_path),
                              "manifest_path": str(manifest_path), "normalized": normalized,
                              "checks": checks})
        except (OSError, ValueError, json.JSONDecodeError) as error:
            integrity_issues.append({"path": str(manifest_path), "reason": "UNREADABLE_ARTIFACT",
                                     "detail": str(error)})

    # Multiple runs may contain the same accession. Count every artifact in the
    # integrity section, and use the lexically latest run for business metrics.
    artifacts.sort(key=lambda item: (Path(item["manifest_path"]).parent.name, item["path"]))
    by_accession = {}
    for artifact in artifacts:
        by_accession[artifact["accession_number"]] = artifact
    normalized_accessions = set(by_accession)
    missing_accessions = sorted(set(ledger_by_accession) - normalized_accessions)
    missing_supported_accessions = sorted(
        accession for accession in missing_accessions
        if Path(ledger_by_accession[accession].get("primary_document") or "").suffix.lower() == ".xml")

    unsupported = []
    batch_summary = None
    failed_batch_accessions = set()
    if batch_summary_path:
        batch_summary = _read_json(Path(batch_summary_path))
        for result in batch_summary.get("results", []):
            if result.get("return_code", 0) == 0:
                continue
            failed_batch_accessions.add(result.get("accession_number"))
    for accession, filing in ledger_by_accession.items():
        document = filing.get("primary_document") or ""
        if Path(document).suffix.lower() in {".htm", ".html"}:
            unsupported.append({"accession_number": accession,
                                "filing_date": filing.get("filing_date"),
                                "primary_document": document,
                                "status": "UNSUPPORTED_SOURCE_FORMAT",
                                "batch_failed": accession in failed_batch_accessions})

    code_counts, class_counts, role_counts = Counter(), Counter(), Counter()
    plan_counts, amendment_counts = Counter(), Counter()
    candidate_counts, candidate_plan_counts = Counter(), Counter()
    quality_flags = Counter()
    transaction_rows = 0
    for accession, artifact in by_accession.items():
        normalized = artifact["normalized"]
        filing = normalized.get("filing", {})
        plan_counts[str(filing.get("aff10b5one"))] += 1
        amendment_counts[str(filing.get("is_amendment"))] += 1
        role_counts.update(_owner_roles(filing.get("reporting_owners", [])))
        for transaction in normalized.get("transactions", []):
            transaction_rows += 1
            code = transaction.get("transaction_code") or "UNKNOWN"
            class_name = transaction.get("transaction_class") or "unknown"
            code_counts[code] += 1
            class_counts[class_name] += 1
            quality_flags.update(transaction.get("quality_flags", []))
            if transaction.get("is_purchase_sale_candidate"):
                direction = "buy" if code == "P" else "sale" if code == "S" else "other"
                candidate_counts[direction] += 1
                candidate_plan_counts[f"{direction}|10b5-1={filing.get('aff10b5one')}"] += 1

    success_count = sum(1 for result in (batch_summary or {}).get("results", [])
                        if result.get("return_code", 0) == 0)
    failure_count = sum(1 for result in (batch_summary or {}).get("results", [])
                        if result.get("return_code", 0) != 0)
    return {
        "schema_version": "form4-history-audit-v1",
        "ledger": {"path": str(ledger_path), "sha256": _sha256(ledger_raw),
                   "form4_count": len(form4_rows)},
        "artifacts": {"manifest_count": len(artifacts), "unique_normalized_accessions": len(normalized_accessions),
                      "duplicate_artifact_count": len(artifacts) - len(normalized_accessions),
                      "missing_accessions": missing_accessions,
                      "missing_supported_accessions": missing_supported_accessions,
                      "integrity_check_counts": dict(Counter(
                          check for item in artifacts for check, passed in item["checks"].items()
                          if passed is True)),
                      "integrity_issues": integrity_issues},
        "batch": {"summary_path": str(batch_summary_path) if batch_summary_path else None,
                  "selected_count": (batch_summary or {}).get("selected_count"),
                  "success_count": success_count if batch_summary is not None else None,
                  "failure_count": failure_count if batch_summary is not None else None,
                  "unsupported_source_format": unsupported},
        "filings": {"transaction_count": transaction_rows,
                    "owner_role_counts": dict(sorted(role_counts.items())),
                    "amendment_counts": dict(sorted(amendment_counts.items())),
                    "10b5_1_plan_counts": dict(sorted(plan_counts.items())),
                    "transaction_code_counts": dict(sorted(code_counts.items())),
                    "transaction_class_counts": dict(sorted(class_counts.items())),
                    "purchase_sale_candidate_counts": dict(sorted(candidate_counts.items())),
                    "candidate_counts_by_plan": dict(sorted(candidate_plan_counts.items())),
                    "quality_flag_counts": dict(sorted(quality_flags.items()))},
    }
