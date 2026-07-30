"""Tests for Phase 1 watchlist seed metadata validation."""

import pytest

from backend.src.scripts.seed_watchlist_handler import (
    REQUIRED_METADATA_FIELDS,
    _build_stock_item,
    _seed_path,
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
        "logo_url": "https://cdn.example.com/logos/AAPL/logo.svg",
        "logo_icon_url": "https://cdn.example.com/logos/AAPL/icon.png",
        "logo_source": "polygon_ticker_details",
        "logo_source_url": "https://api.polygon.io/v3/reference/tickers/AAPL",
        "logo_checked_at": "2026-07-06T08:00:00Z",
    }
    row.update(overrides)
    return row


def test_seed_header_requires_static_metadata_columns():
    with pytest.raises(ValueError, match="company_name"):
        _validate_header(["ticker", "company_size", "source"])


def test_seed_header_accepts_required_metadata_columns():
    _validate_header(sorted(REQUIRED_METADATA_FIELDS))


def test_seed_path_prefers_packaged_backend_seed_csv():
    assert _seed_path().as_posix().endswith("backend/src/data/watchlist_seed.csv")


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
    assert item["logo_icon_url"] == "https://cdn.example.com/logos/AAPL/icon.png"
    assert item["logo_source"] == "polygon_ticker_details"
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
    assert summary == {
        "created": 0,
        "missing": 0,
        "changed": 1,
        "unchanged": 0,
        "invalid": 0,
        "inactive": 0,
        "out_of_scope": 0,
    }
    assert stored["business_description"] == (
        "Designs and sells consumer electronics and services."
    )
    assert stored["flagship_products"] == ["iPhone", "Mac", "Services"]
    assert stored["is_sell_alert_watch"] is True
    assert stored["latest_stock_data_date"] == "2026-06-18"
    assert stored["latest_close_price"] == "213.25"
    assert stored["logo_url"] == "https://cdn.example.com/logos/AAPL/logo.svg"
    assert table.items[("CONFIG#sell_alert_watchlist", "VALUE")]["values"] == ["AAPL"]


def test_sync_static_metadata_does_not_clobber_logo_fields_when_csv_lacks_columns(
    monkeypatch,
):
    existing = _build_stock_item(_complete_row(), set())
    table = _FakeTable([existing])
    row_without_logo_columns = {
        key: value
        for key, value in _complete_row(company_name="Apple Computer Inc.").items()
        if not key.startswith("logo_")
    }
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [row_without_logo_columns],
    )

    summary = sync_static_metadata(table, set())

    stored = table.items[("STOCK#AAPL", "META")]
    assert summary["changed"] == 1
    assert stored["company_name"] == "Apple Computer Inc."
    assert stored["logo_url"] == "https://cdn.example.com/logos/AAPL/logo.svg"
    assert stored["logo_icon_url"] == "https://cdn.example.com/logos/AAPL/icon.png"


def test_sync_static_metadata_creates_missing_stock(monkeypatch):
    table = _FakeTable([])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [_complete_row()],
    )

    summary = sync_static_metadata(table, {"AAPL"})

    assert summary == {
        "created": 1,
        "missing": 1,
        "changed": 0,
        "unchanged": 0,
        "invalid": 0,
        "inactive": 0,
        "out_of_scope": 0,
    }
    assert table.items[("STOCK#AAPL", "META")]["company_name"] == "Apple Inc."


def test_sync_static_metadata_can_skip_invalid_rows(monkeypatch):
    existing = _build_stock_item(_complete_row(sector="Technology"), set())
    table = _FakeTable([existing])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [
            _complete_row(sector="Consumer Discretionary"),
            _complete_row(ticker="BAD", sector=""),
        ],
    )

    summary = sync_static_metadata(table, {"AAPL"}, strict=False)

    stored = table.items[("STOCK#AAPL", "META")]
    assert summary == {
        "created": 0,
        "missing": 0,
        "changed": 1,
        "unchanged": 0,
        "invalid": 1,
        "inactive": 0,
        "out_of_scope": 0,
    }
    assert stored["sector"] == "Consumer Discretionary"


def test_sync_static_metadata_reports_inactive_and_out_of_scope_rows(monkeypatch):
    inactive = _build_stock_item(_complete_row(ticker="OLD"), set())
    inactive["is_active"] = False
    out_of_scope = _build_stock_item(_complete_row(ticker="DRIFT"), set())
    table = _FakeTable([inactive, out_of_scope])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [_complete_row(ticker="AAPL")],
    )

    summary = sync_static_metadata(table, set())

    assert summary["created"] == 1
    assert summary["inactive"] == 1
    assert summary["out_of_scope"] == 1


def test_sync_static_metadata_dry_run_reports_without_writing(monkeypatch):
    existing = _build_stock_item(
        _complete_row(
            business_description="Old description.",
            flagship_products="Old product",
        ),
        set(),
    )
    table = _FakeTable([existing])
    monkeypatch.setattr(
        "backend.src.scripts.seed_watchlist_handler._load_seed_rows",
        lambda: [_complete_row(), _complete_row(ticker="MSFT")],
    )

    summary = sync_static_metadata(table, {"AAPL"}, dry_run=True)

    stored = table.items[("STOCK#AAPL", "META")]
    assert summary["created"] == 1
    assert summary["missing"] == 1
    assert summary["changed"] == 1
    assert stored["business_description"] == "Old description."
    assert ("STOCK#MSFT", "META") not in table.items
    assert ("CONFIG#sell_alert_watchlist", "VALUE") not in table.items


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
    assert result["Data"]["MetadataMissing"] == 0
    assert result["Data"]["MetadataInactive"] == 0
    assert result["Data"]["MetadataOutOfScope"] == 0
    assert stored["sector"] == "Consumer Discretionary"


def test_handler_accepts_shared_repair_request_payload(monkeypatch):
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
            "table_name": "stockara",
            "mode": "sync_static_metadata",
            "run_date": "2026-07-29",
            "tickers": [],
            "max_tickers": 1000,
            "provider_budget": {},
            "dry_run": True,
        },
        None,
    )

    stored = table.items[("STOCK#AAPL", "META")]
    assert result["statusCode"] == 200
    assert result["body"]["changed"] == 1
    assert stored["sector"] == "Technology"


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

    def query(self, **kwargs):
        stocks = [
            dict(item)
            for item in self.items.values()
            if item.get("GSI1PK") == "STOCK"
        ]
        if kwargs.get("Select") == "COUNT":
            return {"Count": len(stocks)}
        return {"Items": stocks}

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
