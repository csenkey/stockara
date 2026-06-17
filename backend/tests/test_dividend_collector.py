"""Unit tests for dividend calendar collection."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd

from backend.src.collectors.dividend_collector import (
    enrich_price_reaction,
    fetch_dividend_events,
    handler,
)


def test_fetch_dividend_events_normalizes_history_and_upcoming_info():
    dividends = pd.Series(
        [0.25, 0.30],
        index=pd.DatetimeIndex(["2026-03-15", "2026-06-15"]),
    )
    ticker = MagicMock()
    ticker.dividends = dividends
    ticker.info = {
        "exDividendDate": int(datetime(2026, 8, 15, tzinfo=timezone.utc).timestamp()),
        "dividendRate": 1.2,
        "dividendYield": 0.015,
    }

    with (
        patch("backend.src.collectors.dividend_collector.yf.Ticker", return_value=ticker),
        patch("backend.src.collectors.dividend_collector.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 6, 17)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        events = fetch_dividend_events("aapl", company_name="Apple", history_limit=2)

    assert events[0]["ticker"] == "AAPL"
    assert events[0]["company_name"] == "Apple"
    assert events[0]["ex_dividend_date"] == date(2026, 3, 15)
    assert events[0]["dividend_amount"] == Decimal("0.25")
    assert events[0]["dividend_yield"] == Decimal("1.500")
    assert events[0]["is_upcoming"] is False
    assert events[-1]["ex_dividend_date"] == date(2026, 8, 15)
    assert events[-1]["dividend_amount"] == Decimal("1.2")
    assert events[-1]["is_upcoming"] is True


def test_enrich_price_reaction_uses_stored_prices_around_past_event():
    event = {
        "ticker": "AAPL",
        "ex_dividend_date": date(2026, 6, 15),
        "is_upcoming": False,
    }

    with patch("backend.src.collectors.dividend_collector.store") as store:
        store.get_stock_data.return_value = [
            {"trading_date": "2026-06-14", "close_price": Decimal("200")},
            {"trading_date": "2026-06-16", "close_price": Decimal("198")},
        ]
        enriched = enrich_price_reaction(event)

    assert enriched["price_before"] == Decimal("200")
    assert enriched["price_after"] == Decimal("198")
    assert enriched["post_ex_dividend_price_move_percent"] == Decimal("-1.00")


@patch("backend.src.collectors.dividend_collector._emit_metric")
@patch("backend.src.collectors.dividend_collector.fetch_dividend_events")
@patch("backend.src.collectors.dividend_collector.DatabasePool")
@patch("backend.src.collectors.dividend_collector.store")
def test_handler_collects_and_stores_events(mock_store, mock_pool, mock_fetch, mock_metric):
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "AAPL", "company_name": "Apple"}
    ]
    mock_fetch.return_value = [
        {
            "ticker": "AAPL",
            "ex_dividend_date": date(2026, 8, 15),
            "is_upcoming": True,
        }
    ]

    result = handler({"max_tickers": 1}, None)

    assert result["statusCode"] == 200
    mock_store.put_dividend_event.assert_called_once()
    mock_metric.assert_any_call("dividend_events_collected", 1)

