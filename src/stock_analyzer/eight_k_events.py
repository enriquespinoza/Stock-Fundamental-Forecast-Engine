"""Extract conservative, timestamped research features from reviewed 8-K sections."""

import csv
import hashlib
import json
import re
from pathlib import Path

from .eight_k import _text

MAX_SECTION_CHARS = 50_000
ITEM_HEADING_RE = re.compile(r"\bitem\s+([1-9]\.\d{2})\s+(?=[A-Z])", re.I)
TARGET_HEADINGS = {
    "2.02": re.compile(r"\bitem\s+2\.02\s+results\s+of\s+operations\s+and\s+financial\s+condition\b", re.I),
    "5.02": re.compile(r"\bitem\s+5\.02\s+departure\s+of\s+directors\s+or\s+certain\s+officers\b", re.I),
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_review(path: Path, accession: str) -> tuple[bytes, dict[str, set[str]]]:
    raw = path.read_bytes()
    reviewed: dict[str, set[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"accession_number", "item", "manual_review_status"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("Review CSV is missing required columns")
        for row in reader:
            if row["accession_number"] != accession:
                continue
            status = row["manual_review_status"].strip().upper()
            if status not in {"CONFIRMED", "NOT_A_SUBSTANTIVE_REFERENCE"}:
                raise ValueError("All filing reference rows must be manually reviewed before feature extraction")
            reviewed.setdefault(row["item"], set()).add(status)
    if not reviewed:
        raise ValueError("Review CSV contains no rows for the normalized accession")
    return raw, reviewed


def _section(text: str, start: int) -> tuple[str, int, int]:
    end_match = ITEM_HEADING_RE.search(text, start + 20)
    end = end_match.start() if end_match else len(text)
    end = min(end, start + MAX_SECTION_CHARS)
    return text[start:end].strip(), start, end


def _heading_and_body(text: str, match: re.Match, section_end: int) -> tuple[str, str, int]:
    """Split the standard item title from narrative text, preserving offsets."""
    heading_end = text.find(".", match.end(), section_end)
    if heading_end < 0:
        heading_end = match.end() - 1
    body_start = heading_end + 1
    while body_start < section_end and text[body_start].isspace():
        body_start += 1
    return text[match.start():body_start].strip(), text[body_start:section_end].strip(), body_start


def _features(item: str, body: str) -> dict:
    result = {"section_body_char_count": len(body)}
    if item == "2.02":
        result.update({
            "body_press_release_mention_present": bool(re.search(r"press\s+release|earnings\s+release", body, re.I)),
            "body_furnished_word_mention_present": bool(re.search(r"\bfurnished\b", body, re.I)),
            "not_deemed_filed_clause_present": bool(re.search(
                r"shall\s+not\s+be\s+deemed\s+[\"'“‘]?filed\b", body, re.I)),
            "body_fiscal_period_mentions": ";".join(sorted(set(re.findall(
                r"\b(?:first|second|third|fourth)\s+fiscal\s+(?:quarter|year)\b", body, re.I)), key=str.lower)),
            "body_exhibit_99_1_mention_present": bool(re.search(r"(?:exhibit|ex\.?)[\s_-]*99[\s_.-]*1\b", body, re.I)),
        })
    else:
        subitems = sorted(set(re.findall(r"\bitem\s+5\.02\s*\(([a-f])\)", body, re.I)))
        result.update({
            "body_subitems_observed": ";".join(subitems),
            "body_departure_vocabulary_mention_present": bool(re.search(r"\b(depart|resign|retir|terminat)\w*\b", body, re.I)),
            "body_appointment_vocabulary_mention_present": bool(re.search(r"\b(appoint|elect|name)\w*\b", body, re.I)),
            "body_compensation_vocabulary_mention_present": bool(re.search(r"\b(compensat|salary|bonus|equity award|severance)\w*\b", body, re.I)),
        })
    return result


def extract_reviewed_8k_features(normalized_path: Path, review_csv: Path, output_dir: Path) -> dict:
    """Extract Item 2.02/5.02 section indicators after manual references are reviewed.

    Source and review artifacts are hash-checked and all features retain filing
    availability. Text cues are indicators only; they do not assign event
    direction, sentiment, or investment meaning.
    """
    normalized_path, review_csv, output_dir = Path(normalized_path), Path(review_csv), Path(output_dir)
    manifest_path = normalized_path.parent / "manifest.json"
    normalized_raw, manifest_raw = normalized_path.read_bytes(), manifest_path.read_bytes()
    doc, manifest = json.loads(normalized_raw), json.loads(manifest_raw)
    if manifest.get("output", {}).get("sha256") != _sha(normalized_raw):
        raise ValueError("Normalized file hash does not match index manifest")
    source_path = Path(manifest.get("source", {}).get("path", ""))
    source = source_path.read_bytes()
    source_sha = _sha(source)
    if source_sha != manifest.get("source", {}).get("sha256") or source_sha != doc.get("source_sha256"):
        raise ValueError("Source hash does not match normalized index and manifest")
    review_raw, reviewed = _load_review(review_csv, doc["accession_number"])
    text = _text(source)
    events = []
    for item, heading_re in TARGET_HEADINGS.items():
        if "CONFIRMED" not in reviewed.get(item, set()):
            continue
        headings = list(heading_re.finditer(text))
        for index, match in enumerate(headings, start=1):
            section, start, end = _section(text, match.start())
            heading_text, body_text, body_start = _heading_and_body(text, match, end)
            events.append({
                "schema_version": "8k-section-event-features-v2",
                "issuer_cik": doc["issuer_cik"], "accession_number": doc["accession_number"],
                "form": doc["form"], "is_amendment": doc["form"] == "8-K/A",
                "filing_date": doc.get("filing_date"),
                "event_available_at_utc": doc.get("historical_available_after_utc"),
                "historical_research_eligible": doc.get("historical_research_eligible", False),
                "item": item, "section_ordinal": index, "section_start_offset": start,
                "section_end_offset": end, "heading_end_offset": body_start,
                "body_start_offset": body_start, "standard_heading_text": heading_text,
                "section_body_text_excerpt": body_text[:2_000],
                "source_sha256": source_sha, "review_csv_sha256": _sha(review_raw),
                "feature_status": "TEXT_MENTIONS_ONLY_UNVERIFIED_EVENT_LABELS",
                **_features(item, body_text),
            })
    output_dir.mkdir(parents=True, exist_ok=False)
    output = output_dir / "8k_section_event_features.jsonl"
    output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in events), encoding="utf-8")
    output_raw = output.read_bytes()
    summary = {
        "schema_version": "8k-section-event-feature-run-v2", "accession_number": doc["accession_number"],
        "source_sha256": source_sha, "normalized_sha256": _sha(normalized_raw),
        "review_csv_sha256": _sha(review_raw), "event_count": len(events),
        "item_counts": {item: sum(row["item"] == item for row in events) for item in TARGET_HEADINGS},
        "output_path": str(output), "output_sha256": _sha(output_raw),
        "research_only": True, "forecast_status": "NO_FORECAST_NO_ORDERS",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
