"""Unit tests for dividend calendar collection."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd

from src.collectors.collection_distributor import build_manifest
from src.models.schemas import CollectionTaskStatus, CollectionTaskType

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


def test_fetch_dividend_events_keeps_five_year_history_window():
    dividends = pd.Series(
        [0.15, 0.20, 0.25],
        index=pd.DatetimeIndex(["2020-01-15", "2022-03-15", "2026-06-15"]),
    )
    ticker = MagicMock()
    ticker.dividends = dividends
    ticker.info = {}

    with (
        patch("backend.src.collectors.dividend_collector.yf.Ticker", return_value=ticker),
        patch("backend.src.collectors.dividend_collector.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 7, 3)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        events = fetch_dividend_events(
            "aapl",
            company_name="Apple",
            start_date=date(2021, 7, 4),
            end_date=date(2026, 10, 31),
            history_limit=80,
        )

    assert [event["ex_dividend_date"] for event in events] == [
        date(2022, 3, 15),
        date(2026, 6, 15),
    ]


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


@patch("backend.src.collectors.dividend_collector.write_manifest")
@patch("backend.src.collectors.dividend_collector.load_manifest")
@patch("backend.src.collectors.dividend_collector._emit_metric")
@patch("backend.src.collectors.dividend_collector.fetch_dividend_events")
@patch("backend.src.collectors.dividend_collector.DatabasePool")
@patch("backend.src.collectors.dividend_collector.store")
def test_handler_processes_manifest_dividend_task(
    mock_store,
    mock_pool,
    mock_fetch,
    mock_metric,
    mock_load_manifest,
    mock_write_manifest,
):
    manifest = build_manifest(
        [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        manifest_date=date(2026, 6, 20),
        generated_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
    )
    task = next(
        candidate
        for candidate in manifest.tasks
        if candidate.task_type == CollectionTaskType.DIVIDEND
    )
    mock_load_manifest.return_value = manifest
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "AAPL", "company_name": "Apple"},
        {"ticker": "MSFT", "company_name": "Microsoft"},
        {"ticker": "NVDA", "company_name": "NVIDIA"},
    ]
    mock_fetch.return_value = [
        {"ticker": "AAPL", "ex_dividend_date": date(2026, 8, 15), "is_upcoming": True}
    ]

    result = handler(
        {
            "mode": "manifest_task",
            "manifest_bucket": "bucket",
            "manifest_key": manifest.s3_key,
            "task_id": task.task_id,
        },
        MagicMock(aws_request_id="request-1"),
    )

    assert result["statusCode"] == 200
    assert result["body"]["selected_ticker_count"] == len(task.tickers)
    assert mock_fetch.call_count == len(task.tickers)
    assert mock_write_manifest.call_count == 2
    assert task.status == CollectionTaskStatus.SUCCEEDED
    assert task.output_counts.records_written == len(task.tickers)
    assert task.output_counts.successful_tickers == len(task.tickers)
