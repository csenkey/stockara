"""Audit watchlist metadata gaps against known cached provider coverage."""

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


REQUIRED_DECISION_GRADE_FIELDS = (
    "ticker",
    "company_name",
    "sector",
    "industry",
    "company_size",
    "source",
    "metadata_source",
    "metadata_source_url",
    "metadata_as_of",
)

REPORT_FIELDNAMES = [
    "ticker",
    "classification",
    "decision_grade_active",
    "missing_required_fields",
    "provider_availability",
    "canonical_ticker",
    "reason",
    "evidence_source",
    "reviewed_at",
]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split()).strip()


def _ticker(value: Any) -> str:
    return _clean(value).upper()


def _symbol_variants(ticker: str) -> list[str]:
    variants = [ticker]
    if "." in ticker:
        variants.extend([ticker.replace(".", "/"), ticker.replace(".", "-")])
    return list(dict.fromkeys(variants))


def required_metadata_gaps(row: dict[str, Any]) -> list[str]:
    """Return required metadata fields missing for decision-grade analysis."""
    return [
        field
        for field in REQUIRED_DECISION_GRADE_FIELDS
        if not _clean(row.get(field))
    ]


def load_seed_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def load_nasdaq_screener(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("data", {}).get("rows", [])
    return {_ticker(row.get("symbol")): row for row in rows if row.get("symbol")}


def load_nasdaq_profiles(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {_ticker(ticker): profile for ticker, profile in data.items() if profile}


def provider_availability(
    ticker: str,
    screener: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """Return provider availability and the symbol variant with evidence."""
    for symbol in _symbol_variants(ticker):
        has_screener = symbol in screener
        has_profile = symbol in profiles
        if has_screener and has_profile:
            return "nasdaq_screener|nasdaq_company_profile", symbol
        if has_profile:
            return "nasdaq_company_profile", symbol
        if has_screener:
            return "nasdaq_screener", symbol
    return "none", ticker


def classify_metadata_row(
    row: dict[str, Any],
    screener: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    reviewed_at: str,
) -> dict[str, str]:
    ticker = _ticker(row.get("ticker"))
    missing = required_metadata_gaps(row)
    availability, canonical_ticker = provider_availability(ticker, screener, profiles)
    decision_grade_active = not missing

    if decision_grade_active:
        classification = "active_canonical"
        reason = "Required decision-grade metadata is present."
    elif "nasdaq_company_profile" in availability:
        classification = "provider_partial_profile"
        reason = "Provider profile evidence exists, but required metadata remains incomplete."
    elif "nasdaq_screener" in availability:
        classification = "provider_screener_only"
        reason = "Provider screener evidence exists, but profile-backed metadata is missing."
    else:
        classification = "missing_provider_coverage"
        reason = "No cached provider evidence was found for this ticker."

    return {
        "ticker": ticker,
        "classification": classification,
        "decision_grade_active": "true" if decision_grade_active else "false",
        "missing_required_fields": "|".join(missing),
        "provider_availability": availability,
        "canonical_ticker": canonical_ticker,
        "reason": reason,
        "evidence_source": "nasdaq_cache" if availability != "none" else "none",
        "reviewed_at": reviewed_at,
    }


def audit_rows(
    rows: list[dict[str, Any]],
    screener: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    reviewed_at: str,
    include_complete: bool = False,
) -> list[dict[str, str]]:
    report_rows = [
        classify_metadata_row(row, screener, profiles, reviewed_at) for row in rows
    ]
    if include_complete:
        return report_rows
    return [row for row in report_rows if row["decision_grade_active"] == "false"]


def write_csv_report(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_markdown_report(
    rows: list[dict[str, str]],
    path: Path,
    seed_row_count: int,
    include_complete: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    class_counts = Counter(row["classification"] for row in rows)
    unresolved = [row for row in rows if row["decision_grade_active"] == "false"]
    with path.open("w", encoding="utf-8") as file:
        file.write("# Watchlist Metadata Audit\n\n")
        file.write(
            "This report classifies watchlist rows against cached provider evidence "
            "before they are allowed into decision-grade Phase 1 analysis.\n\n"
        )
        file.write(f"- Seed rows reviewed: {seed_row_count}\n")
        file.write(f"- Rows included in this report: {len(rows)}\n")
        file.write(f"- Unresolved decision-grade metadata rows: {len(unresolved)}\n")
        file.write(f"- Complete rows included: {'yes' if include_complete else 'no'}\n\n")
        file.write("## Classification Counts\n\n")
        file.write("| Classification | Count |\n")
        file.write("| --- | ---: |\n")
        for classification, count in sorted(class_counts.items()):
            file.write(f"| {classification} | {count} |\n")
        file.write("\n## Unresolved Rows\n\n")
        if not unresolved:
            file.write("No unresolved metadata rows remain.\n")
            return
        file.write(
            "| Ticker | Classification | Provider availability | Missing fields | Reason |\n"
        )
        file.write("| --- | --- | --- | --- | --- |\n")
        for row in unresolved:
            file.write(
                f"| {row['ticker']} | {row['classification']} | "
                f"{row['provider_availability']} | "
                f"{row['missing_required_fields'].replace('|', ', ')} | "
                f"{row['reason']} |\n"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/watchlist_seed.csv")
    parser.add_argument("--output-csv", default="docs/WATCHLIST_METADATA_AUDIT.csv")
    parser.add_argument("--output-md", default="docs/WATCHLIST_METADATA_AUDIT.md")
    parser.add_argument("--nasdaq-screener-cache")
    parser.add_argument("--nasdaq-profile-cache")
    parser.add_argument("--reviewed-at", default=date.today().isoformat())
    parser.add_argument(
        "--include-complete",
        action="store_true",
        help="Include rows that already have all decision-grade metadata fields.",
    )
    parser.add_argument(
        "--fail-on-unresolved",
        action="store_true",
        help="Return a non-zero status when unresolved metadata rows remain.",
    )
    args = parser.parse_args(argv)

    rows = load_seed_rows(Path(args.input))
    screener = load_nasdaq_screener(
        Path(args.nasdaq_screener_cache) if args.nasdaq_screener_cache else None
    )
    profiles = load_nasdaq_profiles(
        Path(args.nasdaq_profile_cache) if args.nasdaq_profile_cache else None
    )
    report_rows = audit_rows(
        rows,
        screener,
        profiles,
        reviewed_at=args.reviewed_at,
        include_complete=args.include_complete,
    )
    write_csv_report(report_rows, Path(args.output_csv))
    write_markdown_report(
        report_rows,
        Path(args.output_md),
        seed_row_count=len(rows),
        include_complete=args.include_complete,
    )

    unresolved_count = sum(
        1 for row in report_rows if row["decision_grade_active"] == "false"
    )
    print(
        f"wrote {len(report_rows)} audit rows; "
        f"{unresolved_count} unresolved decision-grade metadata rows"
    )
    return 1 if args.fail_on_unresolved and unresolved_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
