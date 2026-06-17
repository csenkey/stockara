"""Tests for Phase 1 watchlist seed metadata validation."""

import pytest

from backend.src.scripts.seed_watchlist_handler import (
    REQUIRED_METADATA_FIELDS,
    _build_stock_item,
    _validate_header,
)


def _complete_row(**overrides):
    row = {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "company_size": "blue_chip",
        "source": "sp500",
        "metadata_source": "company_profile",
        "metadata_source_url": "https://www.apple.com/investor-relations/",
        "metadata_as_of": "2026-06-17",
        "business_description": "Designs and sells consumer electronics and services.",
        "flagship_products": "iPhone|Mac|Services",
        "revenue_segments": "Products|Services",
        "primary_customers": "Consumers|Businesses",
        "geographic_exposure": "Americas|Europe|Greater China",
        "competitive_position": "Global premium consumer technology ecosystem.",
        "key_static_risks": "Supply chain concentration|Regulatory pressure",
        "exchange": "NASDAQ",
        "currency": "USD",
        "country": "United States",
        "website": "https://www.apple.com",
        "founded_year": "1976",
        "headquarters": "Cupertino, California",
    }
    row.update(overrides)
    return row


def test_seed_header_requires_static_metadata_columns():
    with pytest.raises(ValueError, match="company_name"):
        _validate_header(["ticker", "company_size", "source"])


def test_seed_header_accepts_required_metadata_columns():
    _validate_header(sorted(REQUIRED_METADATA_FIELDS))


def test_build_stock_item_includes_static_metadata():
    item = _build_stock_item(_complete_row(), {"AAPL"})

    assert item["ticker"] == "AAPL"
    assert item["company_name"] == "Apple Inc."
    assert item["sector"] == "Technology"
    assert item["industry"] == "Consumer Electronics"
    assert item["metadata_source"] == "company_profile"
    assert item["metadata_as_of"] == "2026-06-17"
    assert item["flagship_products"] == ["iPhone", "Mac", "Services"]
    assert item["key_static_risks"] == [
        "Supply chain concentration",
        "Regulatory pressure",
    ]
    assert item["is_sell_alert_watch"] is True


def test_build_stock_item_rejects_missing_required_field():
    with pytest.raises(ValueError, match="missing required metadata field 'sector'"):
        _build_stock_item(_complete_row(sector=""), set())


def test_build_stock_item_rejects_invalid_sector():
    with pytest.raises(ValueError, match="invalid sector"):
        _build_stock_item(_complete_row(sector="Everything"), set())
