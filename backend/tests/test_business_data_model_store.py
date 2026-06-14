"""Tests for additive business-data DynamoDB entity shapes."""

from datetime import date
from decimal import Decimal

import pytest

from backend.src.db.connection import DatabasePool, DynamoStore


class FakeTable:
    def __init__(self):
        self.put_calls = []

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)


@pytest.fixture
def store(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(DatabasePool, "_table", table)
    return DynamoStore()


def last_item(store):
    return store.table.put_calls[-1]["Item"]


def test_put_stock_profile_uses_stock_partition(store):
    store.put_stock_profile(
        {
            "ticker": "AAPL",
            "company_history": "Founded in 1976.",
            "business_description": "Consumer technology company.",
            "leading_products": ["iPhone"],
            "business_stats": {"revenue_growth_pct": Decimal("8.5")},
        }
    )

    item = last_item(store)
    assert item["PK"] == "STOCK#AAPL"
    assert item["SK"] == "PROFILE"
    assert item["entity"] == "stock_profile"


def test_put_dividend_event_uses_ticker_date_key(store):
    store.put_dividend_event(
        {
            "ticker": "MSFT",
            "ex_dividend_date": date(2025, 5, 15),
            "dividend_value": Decimal("0.8300"),
            "currency": "USD",
            "price_impact": {"window_days": 1, "percent_change": Decimal("-0.3571")},
        }
    )

    item = last_item(store)
    assert item["PK"] == "DIVIDEND#MSFT"
    assert item["SK"] == "DATE#2025-05-15"
    assert item["dividend_value"] == Decimal("0.8300")


def test_put_earnings_call_summary_uses_ticker_date_key(store):
    store.put_earnings_call_summary(
        {
            "ticker": "NVDA",
            "call_date": date(2025, 8, 20),
            "fiscal_period": "Q2 FY2026",
            "summary": "Management discussed data-center demand.",
            "key_topics": ["data center"],
            "price_impact": {"window_days": 7, "abnormal_percent_change": Decimal("3.15")},
        }
    )

    item = last_item(store)
    assert item["PK"] == "EARNINGS_CALL#NVDA"
    assert item["SK"] == "DATE#2025-08-20"
    assert item["entity"] == "earnings_call_summary"


def test_put_sector_trend_and_correlation_share_sector_partition(store):
    store.put_sector_trend(
        {
            "sector": "Technology",
            "trend_date": date(2025, 7, 1),
            "benchmark_symbol": "XLK",
            "benchmark_close": Decimal("250.1200"),
            "percent_change": Decimal("1.2300"),
            "trend_score": Decimal("62.5"),
        }
    )
    store.put_sector_ticker_correlation(
        {
            "sector": "Technology",
            "ticker": "AAPL",
            "calculation_date": date(2025, 7, 1),
            "window_days": 90,
            "correlation": Decimal("0.812345"),
            "sample_size": 63,
        }
    )

    trend, correlation = [call["Item"] for call in store.table.put_calls]
    assert trend["PK"] == "SECTOR#Technology"
    assert trend["SK"] == "TREND#DATE#2025-07-01"
    assert correlation["PK"] == "SECTOR#Technology"
    assert correlation["SK"].startswith("CORRELATION#TICKER#AAPL#")


def test_put_suggestion_history_uses_user_date_key(store):
    store.put_suggestion_history(
        user_id="user-1",
        suggestion_date=date(2025, 7, 2),
        analysis_date=date(2025, 7, 1),
        encrypted_data="encrypted-history",
    )

    item = last_item(store)
    assert item["PK"] == "USER#user-1"
    assert item["SK"] == "SUGGESTIONS#DATE#2025-07-02"
    assert item["GSI1PK"] == "SUGGESTIONS#2025-07-02"
    assert item["encrypted_data"] == "encrypted-history"


def test_put_top_pick_uses_static_daily_content_key(store):
    store.put_top_pick(
        {
            "pick_date": date(2025, 7, 2),
            "ticker": "NVDA",
            "company_name": "NVIDIA Corporation",
            "reasoning": "Strong business momentum.",
            "analysis_date": date(2025, 7, 1),
        }
    )

    item = last_item(store)
    assert item["PK"] == "TOP_PICK"
    assert item["SK"] == "DATE#2025-07-02"
    assert item["GSI1PK"] == "TOP_PICK"
