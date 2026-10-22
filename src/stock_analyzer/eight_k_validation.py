"""Validate indexed 8-K item references and emit conservative research features."""

from collections import Counter
from html.parser import HTMLParser
import csv
import hashlib
import json
import re
from pathlib import Path

from .eight_k import ITEM_FAMILIES, ITEM_RE


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _visible_text(raw: bytes) -> str:
    parser = _Text()
    parser.feed(raw.decode("utf-8-sig", errors="replace"))
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_8k_run(normalized_path: Path, output_root: Path, *, built_at_utc: str) -> dict:
    """Re-scan the archived source, compare item counts, and write reviewable rows.

    The output supplies evidence excerpts and explicit item flags. It does not
    infer sentiment, materiality, or the meaning of an event.
    """
    normalized_path = Path(normalized_path)
    manifest_path = normalized_path.parent / "manifest.json"
    normalized_raw = normalized_path.read_bytes()
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    doc = json.loads(normalized_raw)
    if manifest.get("output", {}).get("sha256") != _sha(normalized_raw):
        raise ValueError("Normalized 8-K output hash does not match manifest")
    source_path = Path(manifest.get("source", {}).get("path", ""))
    if not source_path.is_file():
        raise ValueError("Archived source document is unavailable")
    source_raw = source_path.read_bytes()
    source_sha = _sha(source_raw)
    if source_sha != manifest.get("source", {}).get("sha256") or source_sha != doc.get("source_sha256"):
        raise ValueError("Archived source hash does not match index and manifest")
    text = _visible_text(source_raw)
    matches = list(ITEM_RE.finditer(text))
    counts = Counter(m.group(1) for m in matches)
    indexed_counts = {item["item"]: int(item["mention_count"]) for item in doc.get("items", [])}
    reference_match = counts == Counter(indexed_counts)

    references = []
    for ordinal, match in enumerate(matches, start=1):
        code = match.group(1)
        start, end = max(0, match.start() - 100), min(len(text), match.end() + 180)
        references.append({
            "accession_number": doc["accession_number"],
            "issuer_cik": doc["issuer_cik"],
            "form": doc["form"],
            "historical_available_after_utc": doc.get("historical_available_after_utc"),
            "source_sha256": source_sha,
            "item": code,
            "family": ITEM_FAMILIES.get(code, "unclassified_item"),
            "mention_ordinal": ordinal,
            "visible_text_offset": match.start(),
            "context_excerpt": text[start:end],
            "count_matches_index": str(reference_match).lower(),
            "manual_review_status": "PENDING",
        })

    lower = text.lower()
    subitems = sorted(set(re.findall(r"\bitem\s+5\.02\s*\(([a-f])\)", text, re.I)))
    features = {
        "schema_version": "8k-event-features-v1",
        "issuer_cik": doc["issuer_cik"],
        "accession_number": doc["accession_number"],
        "form": doc["form"],
        "filing_date": doc.get("filing_date"),
        "historical_available_after_utc": doc.get("historical_available_after_utc"),
        "historical_research_eligible": doc.get("historical_research_eligible", False),
        "source_sha256": source_sha,
        "item_2_02_present": "2.02" in counts,
        "item_2_02_mention_count": counts.get("2.02", 0),
        "item_2_02_results_title_present": bool(re.search(
            r"item\s+2\.02\s+results\s+of\s+operations\s+and\s+financial\s+condition", text, re.I)),
        "item_2_02_furnished_language_present": bool(re.search(
            r"furnished\s+(?:as|under|pursuant\s+to)\s+(?:item\s+)?2\.02|item\s+2\.02.{0,160}furnished", text, re.I)),
        "exhibit_99_1_reference_present": bool(re.search(r"(?:exhibit|ex\.?)[\s_-]*99[\s_.-]*1\b", text, re.I)),
        "item_5_02_present": "5.02" in counts,
        "item_5_02_mention_count": counts.get("5.02", 0),
        "item_5_02_subitems_observed": ";".join(subitems),
        "item_9_01_exhibit_marker_present": "9.01" in counts,
        "item_9_01_mention_count": counts.get("9.01", 0),
        "item_reference_counts_match_source": reference_match,
        "feature_policy": "explicit filing text indicators only; no sentiment or return inference",
    }
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    references_path = output_root / "item_reference_review.csv"
    feature_path = output_root / "filing_features.json"
    with references_path.open("w", encoding="utf-8", newline="") as stream:
        fields = list(references[0]) if references else ["accession_number", "issuer_cik", "form",
            "historical_available_after_utc", "source_sha256", "item", "family", "mention_ordinal",
            "visible_text_offset", "context_excerpt", "count_matches_index", "manual_review_status"]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(references)
    feature_path.write_text(json.dumps(features, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {
        "schema_version": "8k-validation-run-v1",
        "built_at_utc": built_at_utc,
        "accession_number": doc["accession_number"],
        "normalized_path": str(normalized_path),
        "normalized_sha256": _sha(normalized_raw),
        "index_manifest_sha256": _sha(manifest_raw),
        "source_path": str(source_path),
        "source_sha256": source_sha,
        "source_reference_count": len(matches),
        "indexed_item_counts": dict(sorted(indexed_counts.items())),
        "source_item_counts": dict(sorted(counts.items())),
        "item_reference_counts_match_source": reference_match,
        "manual_review_status": "PENDING",
        "references_csv": str(references_path),
        "references_csv_sha256": _sha(references_path.read_bytes()),
        "features_json": str(feature_path),
        "features_json_sha256": _sha(feature_path.read_bytes()),
        "research_only": True,
    }
    (output_root / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
