"""Unit tests for dividend calendar collection."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import requests

from src.collectors.collection_distributor import build_manifest
from src.models.schemas import CollectionTaskStatus, CollectionTaskType

from backend.src.collectors.dividend_collector import (
    enrich_price_reaction,
    fetch_alpha_vantage_dividend_events,
    fetch_dividend_events,
    fetch_finnhub_dividend_events,
    handler,
    _pace_alpha_vantage_request,
    _select_stocks,
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


def test_fetch_dividend_events_captures_raw_yfinance_provider_rows():
    dividends = pd.Series([0.25], index=pd.DatetimeIndex(["2026-06-15"]))
    ticker = MagicMock()
    ticker.dividends = dividends
    ticker.info = {
        "exDividendDate": int(datetime(2026, 8, 15, tzinfo=timezone.utc).timestamp()),
        "dividendRate": 1.2,
        "dividendYield": 0.015,
    }
    provider_events: list[dict] = []

    with (
        patch("backend.src.collectors.dividend_collector.yf.Ticker", return_value=ticker),
        patch("backend.src.collectors.dividend_collector.date") as mock_date,
    ):
        mock_date.today.return_value = date(2026, 6, 17)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        fetch_dividend_events(
            "aapl",
            company_name="Apple",
            history_limit=1,
            provider_events=provider_events,
        )

    assert len(provider_events) == 2
    assert provider_events[0]["provider"] == "yfinance"
    assert provider_events[0]["ticker"] == "AAPL"
    assert provider_events[0]["ex_dividend_date"] == "2026-06-15"
    assert provider_events[0]["raw_fields"]["dividend_amount"] == 0.25
    assert provider_events[1]["raw_fields"]["source"] == "ticker_info"


@patch("backend.src.collectors.dividend_collector.requests.get")
def test_fetch_finnhub_dividend_events_normalizes_rows(mock_get, monkeypatch):
    monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = [
        {
            "symbol": "AAPL",
            "exDate": "2026-08-15",
            "payDate": "2026-08-22",
            "amount": 0.26,
            "currency": "USD",
        },
        {
            "symbol": "AAPL",
            "exDate": "2026-05-15",
            "amount": 0.25,
        },
    ]
    mock_get.return_value = response

    provider_events: list[dict] = []
    events = fetch_finnhub_dividend_events(
        "aapl",
        company_name="Apple",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 9, 1),
        provider_events=provider_events,
    )

    assert len(events) == 2
    assert events[0]["ticker"] == "AAPL"
    assert events[0]["provider"] == "finnhub"
    assert events[0]["ex_dividend_date"] == date(2026, 8, 15)
    assert events[0]["pay_date"] == "2026-08-22"
    assert events[0]["dividend_amount"] == Decimal("0.26")
    assert provider_events[0]["provider"] == "finnhub"
    assert provider_events[0]["raw_fields"]["currency"] == "USD"
    params = mock_get.call_args.kwargs["params"]
    assert params["symbol"] == "AAPL"
    assert params["from"] == "2026-05-01"
    assert params["to"] == "2026-09-01"


@patch("backend.src.collectors.dividend_collector.logger")
@patch("backend.src.collectors.dividend_collector.requests.get")
def test_fetch_finnhub_dividend_events_treats_http_error_as_unavailable(
    mock_get,
    mock_logger,
    monkeypatch,
):
    monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError(
        "403 Client Error: Forbidden for url: "
        "https://finnhub.io/api/v1/stock/dividend?symbol=AAPL&token=secret-token"
    )
    mock_get.return_value = response

    events = fetch_finnhub_dividend_events("aapl")

    assert events == []
    assert mock_logger.warning.call_args.kwargs["error"].endswith("token=***")


@patch("backend.src.collectors.dividend_collector.requests.get")
def test_fetch_alpha_vantage_dividend_events_normalizes_rows(mock_get, monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "symbol": "AAPL",
        "data": [
            {
                "ex_dividend_date": "2026-08-15",
                "declaration_date": "2026-07-30",
                "record_date": "2026-08-16",
                "payment_date": "2026-08-22",
                "amount": "0.26",
            },
            {
                "ex_dividend_date": "2020-01-15",
                "payment_date": "2020-01-22",
                "amount": "0.20",
            },
        ],
    }
    mock_get.return_value = response

    provider_events: list[dict] = []
    events = fetch_alpha_vantage_dividend_events(
        "aapl",
        company_name="Apple",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        provider_events=provider_events,
    )

    assert len(events) == 1
    assert events[0]["ticker"] == "AAPL"
    assert events[0]["provider"] == "alpha_vantage"
    assert events[0]["ex_dividend_date"] == date(2026, 8, 15)
    assert events[0]["pay_date"] == "2026-08-22"
    assert events[0]["dividend_amount"] == Decimal("0.26")
    assert provider_events[0]["provider"] == "alpha_vantage"
    assert provider_events[0]["raw_fields"]["record_date"] == "2026-08-16"
    params = mock_get.call_args.kwargs["params"]
    assert params["function"] == "DIVIDENDS"
    assert params["symbol"] == "AAPL"


@patch("backend.src.collectors.dividend_collector.logger")
@patch("backend.src.collectors.dividend_collector.requests.get")
def test_fetch_alpha_vantage_dividend_events_handles_provider_note(
    mock_get,
    mock_logger,
    monkeypatch,
):
    import backend.src.collectors.dividend_collector as collector

    monkeypatch.setattr(collector, "_ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED", False)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "Note": (
            "We have detected your API key as secret-alpha-key and our "
            "standard API rate limit is 25 requests per day."
        )
    }
    mock_get.return_value = response

    assert fetch_alpha_vantage_dividend_events("aapl") == []
    assert mock_logger.warning.call_args.kwargs["error"] == (
        "We have detected your API key as *** and our standard API rate "
        "limit is 25 requests per day."
    )
    assert collector._ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED is True


@patch("backend.src.collectors.dividend_collector.logger")
@patch("backend.src.collectors.dividend_collector.requests.get")
def test_fetch_alpha_vantage_dividend_events_skips_when_quota_exhausted(
    mock_get,
    mock_logger,
    monkeypatch,
):
    import backend.src.collectors.dividend_collector as collector

    monkeypatch.setattr(collector, "_ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED", True)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()

    assert fetch_alpha_vantage_dividend_events("aapl") == []
    mock_get.assert_not_called()
    assert mock_logger.warning.call_args.kwargs == {
        "ticker": "AAPL",
        "reason": "quota_exhausted",
    }


@patch("backend.src.collectors.dividend_collector.logger")
@patch("backend.src.collectors.dividend_collector.requests.get")
def test_fetch_alpha_vantage_dividend_events_skips_after_call_budget(
    mock_get,
    mock_logger,
    monkeypatch,
):
    import backend.src.collectors.dividend_collector as collector

    monkeypatch.setattr(collector, "_ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED", False)
    monkeypatch.setattr(collector, "_ALPHA_VANTAGE_DIVIDEND_CALL_COUNT", 1)
    monkeypatch.setattr(collector, "_ALPHA_VANTAGE_DIVIDEND_CALL_BUDGET", 1)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()

    assert fetch_alpha_vantage_dividend_events("aapl") == []
    mock_get.assert_not_called()
    assert mock_logger.warning.call_args.kwargs == {
        "ticker": "AAPL",
        "call_budget": 1,
    }


def test_select_stocks_honors_ticker_offset():
    stocks = [
        {"ticker": "MSFT"},
        {"ticker": "AAPL"},
        {"ticker": "NVDA"},
        {"ticker": "AMZN"},
    ]

    selected = _select_stocks(stocks, {"ticker_offset": 1, "max_tickers": 2})

    assert [stock["ticker"] for stock in selected] == ["AMZN", "MSFT"]


def test_pace_alpha_vantage_request_sleeps_between_configured_calls(monkeypatch):
    import backend.src.collectors.dividend_collector as collector

    monkeypatch.setattr(
        collector,
        "DEFAULT_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS",
        1.25,
    )
    monkeypatch.setattr(collector, "_LAST_ALPHA_VANTAGE_REQUEST_AT", 0.0)
    sleep = MagicMock()
    monkeypatch.setattr(collector.time, "sleep", sleep)
    monotonic_values = iter([100.0, 100.0, 100.5, 101.75])
    monkeypatch.setattr(collector.time, "monotonic", lambda: next(monotonic_values))

    _pace_alpha_vantage_request()
    _pace_alpha_vantage_request()

    sleep.assert_called_once_with(0.75)
    assert collector._LAST_ALPHA_VANTAGE_REQUEST_AT == 101.75


@patch("backend.src.collectors.dividend_collector.fetch_finnhub_dividend_events")
def test_fetch_dividend_events_uses_finnhub_fallback_when_yfinance_empty(mock_finnhub):
    ticker = MagicMock()
    ticker.dividends = pd.Series(dtype=float)
    ticker.info = {}
    mock_finnhub.return_value = [
        {
            "ticker": "AAPL",
            "ex_dividend_date": date(2026, 8, 15),
            "provider": "finnhub",
        }
    ]

    with patch("backend.src.collectors.dividend_collector.yf.Ticker", return_value=ticker):
        events = fetch_dividend_events(
            "aapl",
            company_name="Apple",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )

    assert events == mock_finnhub.return_value
    mock_finnhub.assert_called_once_with(
        "aapl",
        company_name="Apple",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        provider_events=None,
    )


@patch("backend.src.collectors.dividend_collector.fetch_finnhub_dividend_events")
def test_fetch_dividend_events_uses_finnhub_fallback_when_yfinance_history_raises(
    mock_finnhub,
):
    class FailingTicker:
        info = {}

        @property
        def dividends(self):
            raise RuntimeError("429 Client Error: Too Many Requests")

    mock_finnhub.return_value = [
        {
            "ticker": "AAPL",
            "ex_dividend_date": date(2026, 8, 15),
            "provider": "finnhub",
        }
    ]

    with patch(
        "backend.src.collectors.dividend_collector.yf.Ticker",
        return_value=FailingTicker(),
    ):
        events = fetch_dividend_events("aapl", company_name="Apple")

    assert events == mock_finnhub.return_value
    mock_finnhub.assert_called_once()


@patch("backend.src.collectors.dividend_collector.fetch_alpha_vantage_dividend_events")
@patch("backend.src.collectors.dividend_collector.fetch_finnhub_dividend_events")
def test_fetch_dividend_events_uses_alpha_vantage_when_finnhub_empty(
    mock_finnhub,
    mock_alpha_vantage,
):
    ticker = MagicMock()
    ticker.dividends = pd.Series(dtype=float)
    ticker.info = {}
    mock_finnhub.return_value = []
    mock_alpha_vantage.return_value = [
        {
            "ticker": "AAPL",
            "ex_dividend_date": date(2026, 8, 15),
            "provider": "alpha_vantage",
        }
    ]

    with patch("backend.src.collectors.dividend_collector.yf.Ticker", return_value=ticker):
        events = fetch_dividend_events("aapl", company_name="Apple")

    assert events == mock_alpha_vantage.return_value
    mock_finnhub.assert_called_once()
    mock_alpha_vantage.assert_called_once()


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


@patch("backend.src.collectors.dividend_collector.publish_calendar_artifacts")
@patch("backend.src.collectors.dividend_collector.publish_calendar_provider_snapshots")
@patch("backend.src.collectors.dividend_collector._emit_metric")
@patch("backend.src.collectors.dividend_collector.fetch_dividend_events")
@patch("backend.src.collectors.dividend_collector.DatabasePool")
@patch("backend.src.collectors.dividend_collector.store")
def test_handler_marks_zero_provider_rows_as_degraded(
    mock_store,
    mock_pool,
    mock_fetch,
    mock_metric,
    mock_provider_publish,
    mock_calendar_publish,
):
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "AAPL", "company_name": "Apple"},
        {"ticker": "MSFT", "company_name": "Microsoft"},
    ]
    mock_fetch.return_value = []

    result = handler({"max_tickers": 2}, None)

    body = result["body"]
    assert body["status"] == "degraded"
    assert body["events_collected"] == 0
    assert body["zero_event_tickers"] == ["AAPL", "MSFT"]
    assert body["provider_health"]["reason"] == "provider_returned_zero_events"
    mock_metric.assert_any_call("dividend_provider_degraded_runs", 1)
    mock_calendar_publish.assert_called_once()
    assert mock_calendar_publish.call_args.kwargs["collection_status"] == "degraded"
    assert (
        mock_calendar_publish.call_args.kwargs["provider_health"]["reason"]
        == "provider_returned_zero_events"
    )


@patch("backend.src.collectors.dividend_collector.publish_calendar_provider_snapshots")
@patch("backend.src.collectors.dividend_collector.publish_calendar_artifacts")
@patch("backend.src.collectors.dividend_collector.fetch_dividend_events")
@patch("backend.src.collectors.dividend_collector.DatabasePool")
@patch("backend.src.collectors.dividend_collector.store")
def test_handler_supports_repair_calendars_dry_run_for_dividends(
    mock_store,
    mock_pool,
    mock_fetch,
    mock_publish_artifacts,
    mock_publish_snapshots,
):
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "MSFT", "company_name": "Microsoft"},
        {"ticker": "AAPL", "company_name": "Apple"},
        {"ticker": "NVDA", "company_name": "NVIDIA"},
    ]

    result = handler(
        {
            "mode": "repair_calendars",
            "tickers": ["nvda", "aapl"],
            "max_tickers": 1,
            "provider_budget": {"alpha_vantage": 2},
            "dry_run": True,
        },
        None,
    )

    assert result["statusCode"] == 200
    body = result["body"]
    assert body["status"] == "dry_run"
    assert body["mode"] == "repair_calendars"
    assert body["selected_tickers"] == ["AAPL"]
    assert body["provider_budget"] == {"alpha_vantage": 2}
    mock_fetch.assert_not_called()
    mock_store.put_dividend_event.assert_not_called()
    mock_publish_artifacts.assert_not_called()
    mock_publish_snapshots.assert_not_called()


@patch("backend.src.collectors.dividend_collector.write_manifest")
@patch("backend.src.collectors.dividend_collector.load_manifest")
@patch("backend.src.collectors.dividend_collector.publish_calendar_provider_snapshots")
@patch("backend.src.collectors.dividend_collector.publish_calendar_artifacts")
@patch("backend.src.collectors.dividend_collector._emit_metric")
@patch("backend.src.collectors.dividend_collector.fetch_dividend_events")
@patch("backend.src.collectors.dividend_collector.DatabasePool")
@patch("backend.src.collectors.dividend_collector.store")
def test_handler_processes_manifest_dividend_task(
    mock_store,
    mock_pool,
    mock_fetch,
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
    assert mock_publish_artifacts.call_args.kwargs["artifact_scope"] == task.task_id
    assert mock_publish_artifacts.call_args.kwargs["publish_latest"] is False
    assert mock_publish_snapshots.call_args.kwargs["artifact_scope"] == task.task_id
    assert mock_publish_snapshots.call_args.kwargs["publish_latest"] is False
