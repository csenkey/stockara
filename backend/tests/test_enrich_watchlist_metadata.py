"""Tests for watchlist static metadata enrichment helpers."""

from backend.src.scripts.enrich_watchlist_metadata import (
    FIELDNAMES,
    enrich_row,
    required_metadata_gaps,
)


def _row(**overrides):
    row = {field: "" for field in FIELDNAMES}
    row.update(
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "company_size": "blue_chip",
            "source": "sp500",
            "metadata_source": "manual_research",
            "metadata_source_url": "https://www.apple.com/investor-relations/",
            "metadata_as_of": "2026-07-05",
        }
    )
    row.update(overrides)
    return row


def test_required_metadata_gaps_reports_missing_required_metadata():
    assert required_metadata_gaps(
        _row(company_name="", sector="", metadata_source_url="")
    ) == ["company_name", "sector", "metadata_source_url"]


def test_required_metadata_gaps_accepts_complete_required_metadata():
    assert required_metadata_gaps(_row()) == []


def test_enrich_row_fills_required_fields_from_nasdaq_sources():
    screener = {
        "AAPL": {
            "name": "Apple Inc. Common Stock",
            "sector": "Technology",
            "industry": "Computer Manufacturing",
            "country": "United States",
            "ipoyear": "1980",
            "marketCap": "1000",
        }
    }
    profiles = {
        "AAPL": {
            "CompanyName": {"value": "Apple Inc."},
            "Sector": {"value": "Technology"},
            "Industry": {"value": "Computer Manufacturing"},
            "CompanyDescription": {"value": "Apple designs consumer technology."},
            "CompanyUrl": {"value": "https://www.apple.com"},
            "Region": {"value": "North America"},
            "Address": {"value": "Cupertino, California"},
        }
    }

    enriched, missing = enrich_row(
        _row(company_name="", sector="", industry="", metadata_source=""),
        screener,
        profiles,
        "2026-07-05",
    )

    assert missing == []
    assert enriched["company_name"] == "Apple Inc."
    assert enriched["sector"] == "Technology"
    assert enriched["industry"] == "Computer Manufacturing"
    assert enriched["metadata_source"] == "nasdaq_screener|nasdaq_company_profile"
    assert enriched["business_description"] == "Apple designs consumer technology."
