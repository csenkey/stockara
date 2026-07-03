"""Tests for Phase 1 watchlist seed metadata validation."""

import pytest

from backend.src.scripts.seed_watchlist_handler import (
    REQUIRED_METADATA_FIELDS,
    _build_stock_item,
    _validate_header,
    handler,
    sync_static_metadata,
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
        "provider_symbols": "stooq:aapl.us|alpha_vantage:AAPL",
        "provider_symbol_sources": "stooq:manual|alpha_vantage:canonical",
        "provider_symbol_updated_at": "2026-06-20T00:00:00Z",
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
    assert item["provider_symbols"] == {
        "stooq": "aapl.us",
        "alpha_vantage": "AAPL",
    }
    assert item["provider_symbol_sources"]["stooq"] == "manual"
    assert item["provider_symbol_updated_at"] == "2026-06-20T00:00:00Z"
    assert item["is_sell_alert_watch"] is True


def test_build_stock_item_rejects_missing_required_field():
    with pytest.raises(ValueError, match="missing required metadata field 'sector'"):
        _build_stock_item(_complete_row(sector=""), set())


def test_build_stock_item_rejects_invalid_sector():
    with pytest.raises(ValueError, match="invalid sector"):
        _build_stock_item(_complete_row(sector="Everything"), set())


def test_sync_static_metadata_updates_context_without_clobbering_live_fields(monkeypatch):
    existing = _build_stock_item(
        _complete_row(
            business_description="Old description.",
            flagship_products="Old product",
        ),
        set(),
    )
    existing["latest_stock_data_date"] = "2026-06-18"
    existing["latest_close_price"] = "213.25"
    table = _FakeTable([existing])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [_complete_row()],
    )

    summary = sync_static_metadata(table, {"AAPL"})

    stored = table.items[("STOCK#AAPL", "META")]
    assert summary == {"created": 0, "changed": 1, "unchanged": 0, "invalid": 0}
    assert stored["business_description"] == (
        "Designs and sells consumer electronics and services."
    )
    assert stored["flagship_products"] == ["iPhone", "Mac", "Services"]
    assert stored["is_sell_alert_watch"] is True
    assert stored["latest_stock_data_date"] == "2026-06-18"
    assert stored["latest_close_price"] == "213.25"


def test_sync_static_metadata_creates_missing_stock(monkeypatch):
    table = _FakeTable([])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [_complete_row()],
    )

    summary = sync_static_metadata(table, {"AAPL"})

    assert summary == {"created": 1, "changed": 0, "unchanged": 0, "invalid": 0}
    assert table.items[("STOCK#AAPL", "META")]["company_name"] == "Apple Inc."


def test_sync_static_metadata_can_skip_invalid_rows(monkeypatch):
    existing = _build_stock_item(_complete_row(sector="Technology"), set())
    table = _FakeTable([existing])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [_complete_row(sector="Consumer Discretionary"), _complete_row(ticker="BAD", sector="")],
    )

    summary = sync_static_metadata(table, {"AAPL"}, strict=False)

    stored = table.items[("STOCK#AAPL", "META")]
    assert summary == {"created": 0, "changed": 1, "unchanged": 0, "invalid": 1}
    assert stored["sector"] == "Consumer Discretionary"


def test_handler_syncs_existing_metadata_on_custom_resource_update(monkeypatch):
    existing = _build_stock_item(_complete_row(sector="Technology"), set())
    table = _FakeTable([existing])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [_complete_row(sector="Consumer Discretionary")],
    )
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler.boto3.resource",
        lambda _service: _FakeDynamo(table),
    )

    result = handler(
        {
            "RequestType": "Update",
            "ResourceProperties": {
                "TableName": "stockara",
                "SellAlertTickers": "AAPL",
                "SeedHash": "changed",
            },
        },
        None,
    )

    stored = table.items[("STOCK#AAPL", "META")]
    assert result["Data"]["Skipped"] is True
    assert result["Data"]["MetadataChanged"] == 1
    assert stored["sector"] == "Consumer Discretionary"


class _FakeBatch:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def put_item(self, Item):
        self.table.items[(Item["PK"], Item["SK"])] = dict(Item)


class _FakeTable:
    def __init__(self, items):
        self.items = {(item["PK"], item["SK"]): dict(item) for item in items}

    def batch_writer(self):
        return _FakeBatch(self)

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

    def query(self, **_kwargs):
        return {"Count": len(self.items)}

    def update_item(
        self,
        Key,
        UpdateExpression,
        ExpressionAttributeNames,
        ExpressionAttributeValues,
        **_kwargs,
    ):
        item = self.items[(Key["PK"], Key["SK"])]
        set_expression, _, remove_expression = UpdateExpression.partition(" REMOVE ")
        for part in set_expression.removeprefix("SET ").split(", "):
            name, value_name = part.split(" = ")
            item[ExpressionAttributeNames[name]] = ExpressionAttributeValues[value_name]
        for name in remove_expression.split(", ") if remove_expression else []:
            item.pop(ExpressionAttributeNames[name], None)


class _FakeDynamo:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table
