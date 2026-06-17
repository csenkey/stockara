"""Unit tests for earnings calendar collection."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd

from backend.src.collectors.earnings_collector import (
    enrich_price_reaction,
    fetch_earnings_events,
    handler,
)


def test_fetch_earnings_events_normalizes_yfinance_rows():
    rows = pd.DataFrame(
        {
            "EPS Estimate": [2.15, None],
            "Reported EPS": [None, 2.4],
            "Surprise(%)": [None, 5.2],
        },
        index=pd.DatetimeIndex(["2026-07-20", "2026-04-20"]),
    )
    ticker = MagicMock()
    ticker.get_earnings_dates.return_value = rows

    with (
        patch("backend.src.collectors.earnings_collector.yf.Ticker", return_value=ticker),
        patch("backend.src.collectors.earnings_collector.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 6, 17)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        events = fetch_earnings_events("nvda", company_name="NVIDIA")

    assert events[0]["ticker"] == "NVDA"
    assert events[0]["company_name"] == "NVIDIA"
    assert events[0]["event_date"] == date(2026, 7, 20)
    assert events[0]["eps_estimate"] == Decimal("2.15")
    assert events[0]["is_upcoming"] is True
    assert events[1]["reported_eps"] == Decimal("2.4")
    assert events[1]["surprise_percent"] == Decimal("5.2")
    assert events[1]["is_upcoming"] is False


def test_enrich_price_reaction_uses_stored_prices_around_past_event():
    event = {
        "ticker": "NVDA",
        "event_date": date(2026, 4, 20),
        "is_upcoming": False,
    }

    with patch("backend.src.collectors.earnings_collector.store") as store:
        store.get_stock_data.return_value = [
            {"trading_date": "2026-04-19", "close_price": Decimal("100")},
            {"trading_date": "2026-04-21", "close_price": Decimal("110")},
        ]
        enriched = enrich_price_reaction(event)

    assert enriched["price_before"] == Decimal("100")
    assert enriched["price_after"] == Decimal("110")
    assert enriched["post_earnings_price_move_percent"] == Decimal("10.00")


@patch("backend.src.collectors.earnings_collector._emit_metric")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_events")
@patch("backend.src.collectors.earnings_collector.DatabasePool")
@patch("backend.src.collectors.earnings_collector.store")
def test_handler_collects_and_stores_events(mock_store, mock_pool, mock_fetch, mock_metric):
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "NVDA", "company_name": "NVIDIA"}
    ]
    mock_fetch.return_value = [
        {
            "ticker": "NVDA",
            "event_date": date(2026, 7, 20),
            "is_upcoming": True,
        }
    ]

    result = handler({"max_tickers": 1}, None)

    assert result["statusCode"] == 200
    mock_store.put_earnings_event.assert_called_once()
    mock_metric.assert_any_call("earnings_events_collected", 1)

