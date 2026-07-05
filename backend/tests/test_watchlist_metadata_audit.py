"""Tests for watchlist metadata audit reporting."""

from src.scripts.audit_watchlist_metadata import (
    audit_rows,
    classify_metadata_row,
    provider_availability,
    required_metadata_gaps,
)


def _complete_row(ticker: str = "NVDA") -> dict[str, str]:
    return {
        "ticker": ticker,
        "company_name": f"{ticker} Corp",
        "sector": "Technology",
        "industry": "Software",
        "company_size": "blue_chip",
        "source": "seed",
        "metadata_source": "nasdaq_company_profile",
        "metadata_source_url": f"https://example.com/{ticker.lower()}",
        "metadata_as_of": "2026-07-05",
    }


def test_required_metadata_gaps_lists_missing_decision_grade_fields():
    row = _complete_row()
    row["sector"] = ""
    row["metadata_source_url"] = "   "

    assert required_metadata_gaps(row) == ["sector", "metadata_source_url"]


def test_provider_availability_checks_symbol_variants():
    screener = {"BRK/B": {"symbol": "BRK/B"}}
    profiles = {"BF-B": {"CompanyName": {"value": "Brown-Forman"}}}

    assert provider_availability("BRK.B", screener, profiles) == (
        "nasdaq_screener",
        "BRK/B",
    )
    assert provider_availability("BF.B", screener, profiles) == (
        "nasdaq_company_profile",
        "BF-B",
    )
    assert provider_availability("MISSING", screener, profiles) == ("none", "MISSING")


def test_classify_metadata_row_marks_complete_rows_decision_grade_active():
    row = _complete_row("NVDA")

    classified = classify_metadata_row(row, {}, {}, reviewed_at="2026-07-05")

    assert classified["classification"] == "active_canonical"
    assert classified["decision_grade_active"] == "true"
    assert classified["missing_required_fields"] == ""


def test_audit_rows_classifies_unresolved_provider_coverage():
    rows = [
        _complete_row("NVDA"),
        {**_complete_row("GEF"), "sector": "", "metadata_source": ""},
        {**_complete_row("BF.B"), "industry": ""},
        {**_complete_row("OLD"), "company_name": ""},
    ]
    screener = {"GEF": {"symbol": "GEF"}}
    profiles = {"BF-B": {"CompanyName": {"value": "Brown-Forman"}}}

    report = audit_rows(
        rows,
        screener,
        profiles,
        reviewed_at="2026-07-05",
        include_complete=False,
    )

    assert [row["ticker"] for row in report] == ["GEF", "BF.B", "OLD"]
    assert [row["classification"] for row in report] == [
        "provider_screener_only",
        "provider_partial_profile",
        "missing_provider_coverage",
    ]
    assert all(row["decision_grade_active"] == "false" for row in report)


def test_audit_rows_can_include_complete_rows():
    rows = [_complete_row("NVDA"), {**_complete_row("OLD"), "company_name": ""}]

    report = audit_rows(rows, {}, {}, reviewed_at="2026-07-05", include_complete=True)

    assert [row["classification"] for row in report] == [
        "active_canonical",
        "missing_provider_coverage",
    ]
