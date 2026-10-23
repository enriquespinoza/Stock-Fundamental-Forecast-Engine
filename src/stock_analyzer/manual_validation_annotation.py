"""Record human source checks without modifying the frozen validation sample."""

from __future__ import annotations

import csv
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


REVIEW_COLUMNS = {
    "manual_review_status",
    "observed_tag",
    "observed_value",
    "observed_unit",
    "observed_period_start",
    "observed_period_end",
    "review_notes",
}
IDENTITY_COLUMNS = {
    "issuer_cik",
    "accession_number",
    "form",
    "filing_date",
    "metric",
    "tag",
    "value",
    "unit",
    "period_kind",
    "period_start",
    "period_end",
    "source_url",
}


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"CSV must have unique named headers: {path}")
        rows = list(reader)
        if any(None in row for row in rows):
            raise ValueError(f"CSV contains rows with more fields than headers: {path}")
        return list(reader.fieldnames), rows


def _number(value: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", "")).normalize()
    except InvalidOperation as exc:
        raise ValueError(f"Expected a numeric value, got {value!r}") from exc


def _match_row(
    rows: list[dict[str, str]], *, accession_number: str, metric: str,
    tag: str, period_start: str, period_end: str,
) -> dict[str, str]:
    matches = [
        row for row in rows
        if row.get("accession_number") == accession_number
        and row.get("metric") == metric
        and row.get("tag") == tag
        and row.get("period_start", "") == period_start
        and row.get("period_end", "") == period_end
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one row matching review key; found {len(matches)}")
    return matches[0]


def _same_identity(left: dict[str, str], right: dict[str, str]) -> bool:
    return all(left.get(column, "") == right.get(column, "") for column in IDENTITY_COLUMNS)


def annotate_financial_fact(
    sample_path: str | Path,
    output_path: str | Path,
    *,
    accession_number: str,
    metric: str,
    tag: str,
    period_start: str,
    period_end: str,
    observed_tag: str,
    observed_value: str,
    observed_unit: str,
    observed_period_start: str,
    observed_period_end: str,
    review_notes: str,
    status: str = "CONFIRMED",
    source_checked: bool = False,
) -> dict[str, Any]:
    """Create/update a reviewed copy for one source-checked financial fact."""
    if not source_checked:
        raise ValueError("Refusing to annotate until the SEC source has been checked")
    if status not in {"CONFIRMED", "MISMATCH", "UNABLE_TO_VERIFY"}:
        raise ValueError("status must be CONFIRMED, MISMATCH, or UNABLE_TO_VERIFY")

    source = Path(sample_path)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ValueError("Output must be a separate reviewed copy; source sample is immutable")

    sample_headers, sample_rows = _read(source)
    missing = (REVIEW_COLUMNS | IDENTITY_COLUMNS) - set(sample_headers)
    if missing:
        raise ValueError(f"Sample CSV is missing required columns: {sorted(missing)}")
    original = _match_row(
        sample_rows,
        accession_number=accession_number,
        metric=metric,
        tag=tag,
        period_start=period_start,
        period_end=period_end,
    )

    if status == "CONFIRMED":
        if observed_tag.strip() != original["tag"].strip():
            raise ValueError("Cannot confirm: observed_tag differs from sampled tag")
        if _number(observed_value) != _number(original["value"]):
            raise ValueError("Cannot confirm: observed_value differs from sampled value")
        if observed_unit.strip().casefold() != original["unit"].strip().casefold():
            raise ValueError("Cannot confirm: observed_unit differs from sampled unit")
        if observed_period_start.strip() != original["period_start"].strip():
            raise ValueError("Cannot confirm: observed period start differs from sampled period")
        if observed_period_end.strip() != original["period_end"].strip():
            raise ValueError("Cannot confirm: observed period end differs from sampled period")

    if output.exists():
        output_headers, output_rows = _read(output)
        if output_headers != sample_headers or len(output_rows) != len(sample_rows):
            raise ValueError("Reviewed copy schema/row count differs from original sample")
        for source_row, reviewed_row in zip(sample_rows, output_rows):
            if not _same_identity(source_row, reviewed_row):
                raise ValueError("Reviewed copy has altered source/sample identity fields")
        reviewed = _match_row(
            output_rows,
            accession_number=accession_number,
            metric=metric,
            tag=tag,
            period_start=period_start,
            period_end=period_end,
        )
    else:
        output_headers = sample_headers
        output_rows = [dict(row) for row in sample_rows]
        reviewed = _match_row(
            output_rows,
            accession_number=accession_number,
            metric=metric,
            tag=tag,
            period_start=period_start,
            period_end=period_end,
        )

    reviewed.update({
        "manual_review_status": status,
        "observed_tag": observed_tag,
        "observed_value": observed_value,
        "observed_unit": observed_unit,
        "observed_period_start": observed_period_start,
        "observed_period_end": observed_period_end,
        "review_notes": review_notes,
    })

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=output.parent,
            delete=False, suffix=".tmp",
        ) as stream:
            temp_name = stream.name
            writer = csv.DictWriter(stream, fieldnames=output_headers, lineterminator="\n")
            writer.writeheader()
            writer.writerows(output_rows)
        os.replace(temp_name, output)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)

    return {
        "output_path": str(output),
        "row_count": len(output_rows),
        "annotated_accession_number": accession_number,
        "annotated_metric": metric,
        "status": status,
    }
