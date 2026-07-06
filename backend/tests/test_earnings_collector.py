"""Unit tests for earnings calendar collection."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd

from src.collectors.collection_distributor import build_manifest
from src.models.schemas import CollectionTaskStatus, CollectionTaskType

from backend.src.collectors.earnings_collector import (
    enrich_price_reaction,
    fetch_earnings_calendar_events,
    fetch_earnings_events,
    handler,
    _select_stocks,
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


def test_fetch_earnings_events_captures_raw_yfinance_provider_rows():
    rows = pd.DataFrame(
        {"EPS Estimate": [2.15], "Reported EPS": [None]},
        index=pd.DatetimeIndex(["2026-07-20"]),
    )
    ticker = MagicMock()
    ticker.get_earnings_dates.return_value = rows
    provider_events: list[dict] = []

    with (
        patch("backend.src.collectors.earnings_collector.yf.Ticker", return_value=ticker),
        patch("backend.src.collectors.earnings_collector.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 6, 17)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        fetch_earnings_events(
            "nvda",
            company_name="NVIDIA",
            provider_events=provider_events,
        )

    assert provider_events == [
        {
            "provider": "yfinance",
            "ticker": "NVDA",
            "company_name": "NVIDIA",
            "event_date": date(2026, 7, 20),
            "source_url": "https://finance.yahoo.com/quote/NVDA/analysis",
            "raw_fields": {"EPS Estimate": 2.15, "Reported EPS": None},
            "collected_at": provider_events[0]["collected_at"],
        }
    ]


@patch("backend.src.collectors.earnings_collector.requests.get")
def test_fetch_earnings_calendar_events_fetches_date_range_for_watchlist(
    mock_get, monkeypatch
):
    monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "earningsCalendar": [
            {
                "symbol": "AAPL",
                "date": "2026-07-01",
                "epsEstimate": 2.15,
                "hour": "bmo",
            },
            {
                "symbol": "UNTRACKED",
                "date": "2026-07-01",
                "epsEstimate": 1.0,
            },
        ]
    }
    mock_get.return_value = response

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 29)

    provider_events: list[dict] = []
    with patch("backend.src.collectors.earnings_collector.date", FrozenDate):
        events = fetch_earnings_calendar_events(
            [{"ticker": "AAPL", "company_name": "Apple"}],
            lookahead_days=14,
            provider_events=provider_events,
        )

    assert len(events) == 1
    assert events[0]["ticker"] == "AAPL"
    assert events[0]["event_date"] == date(2026, 7, 1)
    assert events[0]["eps_estimate"] == Decimal("2.15")
    assert events[0]["time_of_day"] == "before_market"
    params = mock_get.call_args.kwargs["params"]
    assert params["from"] == "2026-06-29"
    assert params["to"] == "2026-07-13"
    assert provider_events[0]["provider"] == "finnhub"
    assert provider_events[0]["ticker"] == "AAPL"
    assert provider_events[0]["raw_fields"]["epsEstimate"] == 2.15


@patch("backend.src.collectors.earnings_collector.requests.get")
def test_fetch_earnings_calendar_events_defaults_to_four_month_forward_window(
    mock_get, monkeypatch
):
    monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"earningsCalendar": []}
    mock_get.return_value = response

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 29)

    with patch("backend.src.collectors.earnings_collector.date", FrozenDate):
        fetch_earnings_calendar_events([{"ticker": "AAPL", "company_name": "Apple"}])

    params = mock_get.call_args.kwargs["params"]
    assert params["from"] == "2026-06-29"
    assert params["to"] == "2026-10-27"


def test_select_stocks_honors_ticker_offset():
    stocks = [
        {"ticker": "MSFT"},
        {"ticker": "AAPL"},
        {"ticker": "NVDA"},
        {"ticker": "AMZN"},
    ]

    selected = _select_stocks(stocks, {"ticker_offset": 1, "max_tickers": 2})

    assert [stock["ticker"] for stock in selected] == ["AMZN", "MSFT"]


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
@patch("backend.src.collectors.earnings_collector.fetch_earnings_calendar_events")
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
    mock_fetch.assert_called_once()
    mock_metric.assert_any_call("earnings_events_collected", 1)


@patch("backend.src.collectors.earnings_collector.write_manifest")
@patch("backend.src.collectors.earnings_collector.load_manifest")
@patch("backend.src.collectors.earnings_collector.publish_calendar_provider_snapshots")
@patch("backend.src.collectors.earnings_collector.publish_calendar_artifacts")
@patch("backend.src.collectors.earnings_collector._emit_metric")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_calendar_events")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_events")
@patch("backend.src.collectors.earnings_collector.DatabasePool")
@patch("backend.src.collectors.earnings_collector.store")
def test_handler_processes_manifest_earnings_task(
    mock_store,
    mock_pool,
    mock_fetch,
    mock_fetch_calendar,
    mock_metric,
    mock_publish_artifacts,
    mock_publish_snapshots,
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
        if candidate.task_type == CollectionTaskType.EARNINGS
    )
    mock_load_manifest.return_value = manifest
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "AAPL", "company_name": "Apple"},
        {"ticker": "MSFT", "company_name": "Microsoft"},
        {"ticker": "NVDA", "company_name": "NVIDIA"},
    ]
    mock_fetch_calendar.return_value = []
    mock_fetch.side_effect = lambda ticker, **kwargs: [
        {
            "ticker": ticker,
            "event_date": date(2026, 7, 20),
            "is_upcoming": True,
        }
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
    assert mock_publish_artifacts.call_args.kwargs["artifact_scope"] == task.task_id
    assert mock_publish_artifacts.call_args.kwargs["publish_latest"] is False
    assert mock_publish_snapshots.call_args.kwargs["artifact_scope"] == task.task_id
    assert mock_publish_snapshots.call_args.kwargs["publish_latest"] is False


@patch("backend.src.collectors.earnings_collector.write_manifest")
@patch("backend.src.collectors.earnings_collector.load_manifest")
@patch("backend.src.collectors.earnings_collector._emit_metric")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_calendar_events")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_events")
@patch("backend.src.collectors.earnings_collector.DatabasePool")
@patch("backend.src.collectors.earnings_collector.store")
def test_manifest_earnings_task_merges_range_calendar_events(
    mock_store,
    mock_pool,
    mock_fetch,
    mock_fetch_calendar,
    mock_metric,
    mock_load_manifest,
    mock_write_manifest,
):
    manifest = build_manifest(
        [{"ticker": "AAPL"}],
        manifest_date=date(2026, 6, 20),
        generated_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
    )
    task = next(
        candidate
        for candidate in manifest.tasks
        if candidate.task_type == CollectionTaskType.EARNINGS
    )
    mock_load_manifest.return_value = manifest
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "AAPL", "company_name": "Apple"},
    ]
    mock_fetch_calendar.return_value = [
        {
            "ticker": "AAPL",
            "event_date": date(2026, 7, 25),
            "eps_estimate": Decimal("2.40"),
            "is_upcoming": True,
            "provider": "finnhub",
        },
    ]
    mock_fetch.return_value = [
        {
            "ticker": "AAPL",
            "event_date": date(2026, 7, 20),
            "eps_estimate": Decimal("2.35"),
            "is_upcoming": True,
            "provider": "yfinance",
        },
        {
            "ticker": "AAPL",
            "event_date": date(2026, 7, 25),
            "eps_estimate": Decimal("2.45"),
            "is_upcoming": True,
            "provider": "yfinance",
        },
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
    assert mock_store.put_earnings_event.call_count == 2
    stored_dates = {
        call.args[0]["event_date"] for call in mock_store.put_earnings_event.call_args_list
    }
    assert stored_dates == {date(2026, 7, 20), date(2026, 7, 25)}
    assert task.output_counts.records_written == 2
