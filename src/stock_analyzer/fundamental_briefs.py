"""Point-in-time, descriptive issuer briefs from normalized SEC facts."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from .financials import METRICS
from .ledger import normalize_cik, parse_timestamp, utc_text
from .point_in_time import REPORT_FAMILIES, _report_family_facts
from .point_in_time_universe import _load_financial_histories

METRIC_ORDER = [*METRICS, "free_cash_flow"]
RATIO_PAIRS = (
    ("net_income", "revenue", "net_income_margin"),
    ("operating_income", "revenue", "operating_margin"),
    ("operating_cash_flow", "revenue", "operating_cash_flow_to_revenue"),
    ("free_cash_flow", "revenue", "free_cash_flow_to_revenue"),
    ("liabilities", "assets", "liabilities_to_assets"),
    ("equity", "assets", "equity_to_assets"),
    ("cash", "assets", "cash_to_assets"),
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _metric_series(rows: list[dict], metric: str, family: str) -> list[dict]:
    definition = REPORT_FAMILIES[family]
    eligible = [row for row in rows if row.get("metric") == metric
                and row.get("form") in definition["forms"]
                and (row.get("period_kind") == "instant"
                     or row.get("period_kind") == definition["duration_kind"])]
    # select_as_of has already resolved amendments for each exact fact period.
    return sorted(eligible, key=lambda row: (row["period_end"], row.get("period_start") or ""), reverse=True)


def _fact_view(row: dict | None, as_of_time) -> dict:
    if row is None:
        return {"status": "INSUFFICIENT_DATA", "value": None, "unit": None,
                "period_start": None, "period_end": None, "form": None,
                "tag": None, "accession_number": None, "available_after_utc": None,
                "age_days": None}
    available = parse_timestamp(row["historical_available_after_utc"])
    if available > as_of_time:
        raise ValueError("Point-in-time brief selected a future financial fact")
    return {"status": "AVAILABLE", "value": row["value"], "unit": row["unit"],
            "period_start": row.get("period_start"), "period_end": row.get("period_end"),
            "form": row["form"], "tag": row.get("tag"),
            "accession_number": row["accession_number"],
            "available_after_utc": utc_text(available),
            "source_url": row.get("source_url"),
            "age_days": round((as_of_time - available).total_seconds() / 86400, 2)}


def _latest_and_prior(rows: list[dict], metric: str, family: str, as_of_time) -> dict:
    series = _metric_series(rows, metric, family)
    latest, prior = (series + [None, None])[:2]
    result = {"latest": _fact_view(latest, as_of_time), "prior": _fact_view(prior, as_of_time),
              "change_percent": None}
    if latest and prior and latest["unit"] == prior["unit"]:
        current, previous = Decimal(latest["value"]), Decimal(prior["value"])
        if previous != 0 and latest.get("period_start") and prior.get("period_start"):
            days = (parse_timestamp(latest["period_end"] + "T00:00:00Z") -
                    parse_timestamp(prior["period_end"] + "T00:00:00Z")).days
            # Compare adjacent fiscal years, not a distant period or another quarter.
            if 330 <= days <= 400:
                result["change_percent"] = float((current / previous - 1) * 100)
    return result


def _aligned_ratios(rows: list[dict], family: str) -> dict:
    metrics = {}
    for metric in METRIC_ORDER:
        metrics[metric] = _metric_series(rows, metric, family)
    ratios = {}
    for numerator, denominator, name in RATIO_PAIRS:
        top = metrics[numerator][0] if metrics[numerator] else None
        bottom = metrics[denominator][0] if metrics[denominator] else None
        value = None
        if (top and bottom and top["unit"] == bottom["unit"]
                and (top.get("period_start"), top["period_end"]) ==
                    (bottom.get("period_start"), bottom["period_end"])
                and Decimal(bottom["value"]) != 0):
            value = float(Decimal(top["value"]) / Decimal(bottom["value"]))
        ratios[name] = {"value": value,
                        "period_start": top.get("period_start") if top else None,
                        "period_end": top.get("period_end") if top else None,
                        "numerator_accession": top.get("accession_number") if top else None,
                        "denominator_accession": bottom.get("accession_number") if bottom else None,
                        "status": "AVAILABLE" if value is not None else "INSUFFICIENT_ALIGNED_FACTS"}
    return ratios


def _review_signals(rows: list[dict], annual: dict[str, list[dict]]) -> list[dict]:
    signals = []
    for metric, name, text in (
        ("operating_cash_flow", "TWO_LATEST_ANNUAL_CFO_PERIODS_NEGATIVE",
         "Operating cash flow is negative in the two latest annual periods."),
        ("free_cash_flow", "TWO_LATEST_ANNUAL_FCF_PERIODS_NEGATIVE",
         "Free cash flow is negative in the two latest annual periods."),
        ("operating_income", "TWO_LATEST_ANNUAL_OPERATING_LOSSES",
         "Operating income is negative in the two latest annual periods."),
    ):
        series = annual[metric]
        if len(series) >= 2 and all(Decimal(row["value"]) < 0 for row in series[:2]):
            signals.append({"code": name, "status": "FLAG_FOR_REVIEW", "description": text,
                            "periods": [row["period_end"] for row in series[:2]],
                            "accessions": [row["accession_number"] for row in series[:2]]})
    revenue = annual["revenue"]
    if len(revenue) >= 3 and all(Decimal(revenue[i]["value"]) < Decimal(revenue[i + 1]["value"])
                                 for i in range(2)):
        signals.append({"code": "REVENUE_DECLINED_TWICE", "status": "FLAG_FOR_REVIEW",
                        "description": "Revenue declined across the two latest annual comparisons.",
                        "periods": [row["period_end"] for row in revenue[:3]],
                        "accessions": [row["accession_number"] for row in revenue[:3]]})
    assets = _metric_series(rows, "assets", "10k")
    liabilities = _metric_series(rows, "liabilities", "10k")
    if assets and liabilities and assets[0]["period_end"] == liabilities[0]["period_end"]:
        if Decimal(liabilities[0]["value"]) > Decimal(assets[0]["value"]):
            signals.append({"code": "LIABILITIES_EXCEED_ASSETS", "status": "FLAG_FOR_REVIEW",
                            "description": "Reported liabilities exceed reported assets; review the filing context and equity position.",
                            "periods": [assets[0]["period_end"]],
                            "accessions": [assets[0]["accession_number"], liabilities[0]["accession_number"]]})
    return signals


def _insights(family: str, metrics: dict, ratios: dict) -> list[str]:
    labels = {"revenue": "Revenue", "net_income": "Net income",
              "operating_income": "Operating income", "operating_cash_flow": "Operating cash flow",
              "free_cash_flow": "Free cash flow"}
    insights = []
    for metric, label in labels.items():
        values = metrics[metric]
        change = values["change_percent"]
        latest = values["latest"]
        prior = values["prior"]
        if change is None:
            continue
        movement = "increased" if change > 0 else "decreased" if change < 0 else "was unchanged"
        insights.append(f"{family.upper()} {label.lower()} {movement} by {abs(change):.1f}% from "
                        f"{prior['period_end']} to {latest['period_end']}.")
    margin = ratios["operating_margin"]
    if margin["value"] is not None:
        insights.append(f"{family.upper()} operating margin was {margin['value'] * 100:.1f}% "
                        f"for the period ending {margin['period_end']}.")
    return insights


def build_fundamental_briefs(financials_root: Path, universe_path: Path, as_of_utc: str,
                             output_root: Path, *, built_at_utc: str | None = None) -> dict:
    financials_root, universe_path, output_root = map(Path, (financials_root, universe_path, output_root))
    as_of_time = parse_timestamp(as_of_utc)
    as_of_text = utc_text(as_of_time)
    universe_raw = universe_path.read_bytes()
    universe = list(csv.DictReader(universe_raw.decode("utf-8-sig").splitlines()))
    if not universe or not {"symbol", "issuer_cik"}.issubset(universe[0]):
        raise ValueError("Universe CSV must contain symbol and issuer_cik columns")
    histories = _load_financial_histories(financials_root)
    briefs = []
    missing_histories = []
    for issuer in universe:
        cik = normalize_cik(issuer["issuer_cik"])
        symbol = issuer["symbol"].strip().upper()
        if cik not in histories:
            missing_histories.append({"symbol": symbol, "issuer_cik": cik})
            continue
        source_path, source_raw, source_manifest, normalized = histories[cik]
        selected_annual = _report_family_facts(normalized.get("facts", []), as_of_text, "10k")
        selected_quarterly = _report_family_facts(normalized.get("facts", []), as_of_text, "10q")
        annual = {metric: _metric_series(selected_annual, metric, "10k") for metric in METRIC_ORDER}
        annual_metrics = {metric: _latest_and_prior(selected_annual, metric, "10k", as_of_time)
                          for metric in METRIC_ORDER}
        quarterly_metrics = {metric: _latest_and_prior(selected_quarterly, metric, "10q", as_of_time)
                             for metric in METRIC_ORDER}
        annual_ratios = _aligned_ratios(selected_annual, "10k")
        quarterly_ratios = _aligned_ratios(selected_quarterly, "10q")
        latest_annual_end = max((row["period_end"] for row in selected_annual), default=None)
        latest_quarter_end = max((row["period_end"] for row in selected_quarterly), default=None)
        flags = _review_signals(selected_annual, annual)
        briefs.append({
            "schema_version": "issuer-fundamental-brief-v1", "issuer_symbol": symbol,
            "issuer_cik": cik, "as_of_utc": as_of_text,
            "filings": {"latest_annual_period_end": latest_annual_end,
                        "latest_quarter_period_end": latest_quarter_end},
            "annual_10k": {"metrics": annual_metrics,
                            "period_aligned_ratios": annual_ratios,
                            "insights": _insights("10-K", annual_metrics, annual_ratios)},
            "quarterly_10q": {"metrics": quarterly_metrics,
                               "period_aligned_ratios": quarterly_ratios,
                               "insights": _insights("10-Q", quarterly_metrics, quarterly_ratios)},
            "review_signals": flags,
            "status": "REVIEW_SIGNALS_PRESENT" if flags else "NO_RULE_TRIGGERED",
            "data_quality": {"annual_metrics_available": sum(v["latest"]["status"] == "AVAILABLE" for v in annual_metrics.values()),
                             "quarterly_metrics_available": sum(v["latest"]["status"] == "AVAILABLE" for v in quarterly_metrics.values())},
            "source": {"normalized_financials_path": str(source_path),
                       "normalized_financials_sha256": _sha(source_raw),
                       "financial_manifest_sha256": _sha((source_path.parent / "manifest.json").read_bytes()),
                       "facts_snapshot_sha256": source_manifest.get("companyfacts_sha256") or source_manifest.get("facts_sha256")},
        })
    stamp = utc_text(parse_timestamp(built_at_utc)) if built_at_utc else utc_text(datetime.now(timezone.utc))
    out_dir = output_root / stamp.replace(":", "-")
    out_dir.mkdir(parents=True, exist_ok=False)
    briefs_path = out_dir / "issuer_fundamental_briefs.jsonl"
    with briefs_path.open("w", encoding="utf-8", newline="") as stream:
        for brief in briefs:
            stream.write(json.dumps(brief, sort_keys=True) + "\n")
    signal_rows = [{"issuer_symbol": brief["issuer_symbol"], "issuer_cik": brief["issuer_cik"],
                    **signal} for brief in briefs for signal in brief["review_signals"]]
    signals_path = out_dir / "review_signals.csv"
    signal_fields = ["issuer_symbol", "issuer_cik", "code", "status", "description", "periods", "accessions"]
    with signals_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=signal_fields, lineterminator="\n")
        writer.writeheader()
        for row in signal_rows:
            writer.writerow({**row, "periods": json.dumps(row["periods"]), "accessions": json.dumps(row["accessions"])})
    summary = {"schema_version": "issuer-fundamental-brief-summary-v1",
               "research_status": "DESCRIPTIVE_RESEARCH_ONLY_NO_ALLOCATION_EFFECT",
               "as_of_utc": as_of_text, "universe_count": len(universe), "brief_count": len(briefs),
               "missing_financial_histories": missing_histories,
               "review_signal_count": len(signal_rows),
               "issuer_status_counts": {status: sum(b["status"] == status for b in briefs)
                                        for status in ("REVIEW_SIGNALS_PRESENT", "NO_RULE_TRIGGERED")},
               "limitations": ["Rules are unvalidated review prompts, not risk ratings or investment advice.",
                              "This narrow fact set does not measure all liquidity, debt, governance, or industry risks.",
                              "Company-specific XBRL tags outside the current normalized mapping may be missing."]}
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"schema_version": "issuer-fundamental-brief-manifest-v1", "built_at_utc": stamp,
                "as_of_utc": as_of_text,
                "universe_input": {"path": str(universe_path), "sha256": _sha(universe_raw)},
                "financials_root": str(financials_root),
                "output": {"path": str(briefs_path), "sha256": _sha(briefs_path.read_bytes()),
                           "row_count": len(briefs)},
                "signals_output": {"path": str(signals_path), "sha256": _sha(signals_path.read_bytes()),
                                   "row_count": len(signal_rows)},
                "summary_sha256": _sha(summary_path.read_bytes()),
                "research_status": "DESCRIPTIVE_RESEARCH_ONLY_NO_ALLOCATION_EFFECT"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": out_dir, "summary": summary, "manifest": manifest}
