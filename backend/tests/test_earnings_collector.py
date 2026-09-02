"""Unit tests for earnings calendar collection."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd

from src.collectors.collection_distributor import build_manifest
from src.models.schemas import CollectionTaskType

from backend.src.collectors.earnings_collector import (
    ManifestTaskRun,
    _collect_per_ticker,
    _complete_manifest_task_run,
    _is_full_watchlist_selection,
    confirm_near_term_earnings_conflicts,
    enrich_price_reaction,
    fetch_alpha_vantage_earnings_calendar_events,
    fetch_alpha_vantage_earnings_events,
    fetch_earnings_calendar_events,
    fetch_earnings_events,
    handler,
    reconcile_earnings_events,
    _select_stocks,
    _select_rotating_fallback_stocks,
)


def test_full_watchlist_selection_uses_actual_ticker_set():
    stocks = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]

    assert _is_full_watchlist_selection(stocks, list(reversed(stocks))) is True
    assert _is_full_watchlist_selection(stocks, [{"ticker": "AAPL"}]) is False
    assert _is_full_watchlist_selection([], []) is False


def test_per_ticker_collection_counts_budget_skip_as_failure():
    attempts: dict[str, dict] = {}
    outcomes: dict[str, str] = {}

    def budget_skipped(_ticker, **kwargs):
        kwargs["provider_attempts"]["alpha_vantage"] = {
            "attempt_count": 1,
            "event_count": 0,
            "raw_event_count": 0,
            "statuses": {"budget_exhausted": 1},
        }
        return []

    with patch(
        "backend.src.collectors.earnings_collector.fetch_earnings_events",
        side_effect=budget_skipped,
    ):
        events, failed = _collect_per_ticker(
            [{"ticker": "AAPL"}],
            {},
            MagicMock(),
            range_start=date(2024, 1, 1),
            range_end=date(2026, 9, 2),
            provider_events=[],
            provider_attempts=attempts,
            ticker_collection_outcomes=outcomes,
            include_range_calendar=False,
        )

    assert events == []
    assert failed == ["AAPL"]
    assert outcomes == {"AAPL": "budget_exhausted"}
    assert attempts["alpha_vantage"]["statuses"] == {"budget_exhausted": 1}


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
def test_fetch_alpha_vantage_earnings_events_normalizes_quarterly_rows(
    mock_get, monkeypatch
):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "symbol": "AAPL",
        "quarterlyEarnings": [
            {
                "fiscalDateEnding": "2026-03-31",
                "reportedDate": "2026-04-30",
                "reportedEPS": "1.65",
                "estimatedEPS": "1.60",
                "surprisePercentage": "3.125",
            },
            {
                "fiscalDateEnding": "2021-03-31",
                "reportedDate": "2021-04-28",
                "reportedEPS": "1.40",
            },
        ],
    }
    mock_get.return_value = response
    provider_events: list[dict] = []

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 17)

    with patch("backend.src.collectors.earnings_collector.date", FrozenDate):
        events = fetch_alpha_vantage_earnings_events(
            "aapl",
            company_name="Apple",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            provider_events=provider_events,
        )

    assert len(events) == 1
    assert events[0]["ticker"] == "AAPL"
    assert events[0]["company_name"] == "Apple"
    assert events[0]["event_date"] == date(2026, 4, 30)
    assert events[0]["reported_eps"] == Decimal("1.65")
    assert events[0]["eps_estimate"] == Decimal("1.6")
    assert events[0]["surprise_percent"] == Decimal("3.125")
    assert events[0]["is_upcoming"] is False
    assert events[0]["provider"] == "alpha_vantage"
    assert provider_events[0]["provider"] == "alpha_vantage"
    assert provider_events[0]["raw_fields"]["reportedEPS"] == "1.65"
    params = mock_get.call_args.kwargs["params"]
    assert params["function"] == "EARNINGS"
    assert params["symbol"] == "AAPL"


@patch("backend.src.collectors.earnings_collector.fetch_alpha_vantage_earnings_events")
def test_fetch_earnings_events_uses_alpha_vantage_when_yfinance_empty(
    mock_alpha,
):
    ticker = MagicMock()
    ticker.get_earnings_dates.return_value = None
    mock_alpha.return_value = [
        {
            "ticker": "AAPL",
            "event_date": date(2026, 4, 30),
            "provider": "alpha_vantage",
        }
    ]

    with patch("backend.src.collectors.earnings_collector.yf.Ticker", return_value=ticker):
        events = fetch_earnings_events("aapl", company_name="Apple")

    assert events == mock_alpha.return_value
    mock_alpha.assert_called_once()
    assert mock_alpha.call_args.args[0] == "aapl"
    assert mock_alpha.call_args.kwargs["company_name"] == "Apple"


@patch("backend.src.collectors.earnings_collector.requests.get")
def test_fetch_alpha_vantage_earnings_events_treats_provider_error_as_empty(
    mock_get, monkeypatch
):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"Information": "standard API call frequency"}
    mock_get.return_value = response

    events = fetch_alpha_vantage_earnings_events("aapl")

    assert events == []


@patch("backend.src.collectors.earnings_collector.requests.get")
def test_fetch_alpha_vantage_global_calendar_uses_one_request_and_captures_raw_rows(
    mock_get, monkeypatch
):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.text = "\n".join(
        [
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay",
            "AAPL,Apple Inc.,2026-07-30,2026-06-30,1.42,USD,post-market",
            "MSFT,Microsoft Corp.,2026-07-31,2026-06-30,3.12,USD,pre-market",
            "AAPL,Apple Inc.,2027-01-30,2026-12-31,1.70,USD,post-market",
        ]
    )
    mock_get.return_value = response
    provider_events: list[dict] = []
    provider_attempts: dict[str, dict] = {}

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 29)

    with (
        patch("backend.src.collectors.earnings_collector.date", FrozenDate),
        patch(
            "backend.src.collectors.earnings_collector._ALPHA_VANTAGE_EARNINGS_CALL_COUNT",
            0,
        ),
        patch(
            "backend.src.collectors.earnings_collector._ALPHA_VANTAGE_EARNINGS_CALL_BUDGET",
            20,
        ),
        patch(
            "backend.src.collectors.earnings_collector._ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED",
            False,
        ),
    ):
        events = fetch_alpha_vantage_earnings_calendar_events(
            [{"ticker": "AAPL", "company_name": "Apple"}],
            start_date=date(2026, 6, 29),
            end_date=date(2026, 10, 27),
            provider_events=provider_events,
            provider_attempts=provider_attempts,
        )

    assert len(events) == 1
    assert events[0]["ticker"] == "AAPL"
    assert events[0]["event_date"] == date(2026, 7, 30)
    assert events[0]["eps_estimate"] == Decimal("1.42")
    assert events[0]["provider"] == "alpha_vantage_calendar"
    assert events[0]["time_of_day"] == "after_market"
    assert provider_events[0]["raw_fields"]["fiscalDateEnding"] == "2026-06-30"
    assert provider_attempts["alpha_vantage_calendar"]["raw_event_count"] == 3
    mock_get.assert_called_once()
    params = mock_get.call_args.kwargs["params"]
    assert params["function"] == "EARNINGS_CALENDAR"
    assert params["horizon"] == "6month"
    assert "symbol" not in params


@patch(
    "backend.src.collectors.earnings_collector.fetch_alpha_vantage_earnings_calendar_events"
)
@patch("backend.src.collectors.earnings_collector.requests.get")
def test_fetch_calendar_retains_provider_conflicts_without_duplicate_exact_dates(
    mock_get, mock_alpha, monkeypatch
):
    monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    mock_alpha.return_value = [
        {
            "ticker": "AAPL",
            "event_date": date(2026, 7, 1),
            "provider": "alpha_vantage_calendar",
        },
        {
            "ticker": "AAPL",
            "event_date": date(2026, 7, 2),
            "provider": "alpha_vantage_calendar",
        },
    ]
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "earningsCalendar": [{"symbol": "AAPL", "date": "2026-07-01"}]
    }
    mock_get.return_value = response

    events = fetch_earnings_calendar_events(
        [{"ticker": "AAPL", "company_name": "Apple"}],
        start_date=date(2026, 6, 29),
        end_date=date(2026, 7, 15),
    )

    assert [(event["event_date"], event["provider"]) for event in events] == [
        (date(2026, 7, 1), "finnhub"),
        (date(2026, 7, 2), "alpha_vantage_calendar"),
    ]


@patch(
    "backend.src.collectors.earnings_collector.fetch_alpha_vantage_earnings_calendar_events"
)
@patch("backend.src.collectors.earnings_collector.requests.get")
def test_fetch_calendar_provider_health_counts_finnhub_before_merge(
    mock_get, mock_alpha, monkeypatch
):
    monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    mock_alpha.return_value = [
        {
            "ticker": "MSFT",
            "event_date": date(2026, 7, 2),
            "provider": "alpha_vantage_calendar",
        }
    ]
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "earningsCalendar": [{"symbol": "AAPL", "date": "2026-07-01"}]
    }
    mock_get.return_value = response
    attempts: dict[str, dict] = {}

    events = fetch_earnings_calendar_events(
        [
            {"ticker": "AAPL", "company_name": "Apple"},
            {"ticker": "MSFT", "company_name": "Microsoft"},
        ],
        start_date=date(2026, 6, 29),
        end_date=date(2026, 7, 15),
        provider_attempts=attempts,
    )

    assert len(events) == 2
    assert attempts["finnhub"]["event_count"] == 1


def test_reconcile_earnings_events_confirms_exact_provider_match():
    events = reconcile_earnings_events(
        [
            {
                "ticker": "AAPL",
                "event_date": date(2026, 7, 30),
                "provider": "finnhub",
                "eps_estimate": Decimal("1.40"),
                "revenue_estimate": Decimal("90000000000"),
            },
            {
                "ticker": "AAPL",
                "event_date": date(2026, 7, 30),
                "provider": "alpha_vantage_calendar",
                "eps_estimate": Decimal("1.42"),
                "fiscal_period_end": date(2026, 6, 30),
            },
        ]
    )

    assert len(events) == 1
    assert events[0]["reconciliation_status"] == "confirmed"
    assert events[0]["date_confidence"] == "high"
    assert events[0]["candidate_dates"] == [date(2026, 7, 30)]
    assert events[0]["fiscal_period_end"] == date(2026, 6, 30)
    assert len(events[0]["observation_ids"]) == 2


def test_reconcile_earnings_events_retains_close_conflicting_dates():
    events = reconcile_earnings_events(
        [
            {
                "ticker": "AAPL",
                "event_date": date(2026, 7, 30),
                "provider": "finnhub",
            },
            {
                "ticker": "AAPL",
                "event_date": date(2026, 8, 1),
                "provider": "alpha_vantage_calendar",
            },
        ]
    )

    assert len(events) == 2
    assert {event["event_date"] for event in events} == {
        date(2026, 7, 30),
        date(2026, 8, 1),
    }
    assert {event["reconciliation_status"] for event in events} == {"conflicting"}
    assert events[0]["canonical_event_id"] == events[1]["canonical_event_id"]
    assert events[0]["candidate_dates"] == [
        date(2026, 7, 30),
        date(2026, 8, 1),
    ]


def test_reconcile_earnings_events_keeps_well_separated_quarters_independent():
    events = reconcile_earnings_events(
        [
            {
                "ticker": "AAPL",
                "event_date": date(2026, 9, 1),
                "provider": "alpha_vantage_calendar",
            },
            {
                "ticker": "AAPL",
                "event_date": date(2026, 12, 1),
                "provider": "finnhub",
            },
        ]
    )

    assert len(events) == 2
    assert {event["reconciliation_status"] for event in events} == {
        "single_source"
    }
    assert len({event["canonical_event_id"] for event in events}) == 2


def test_reconcile_earnings_events_is_idempotent_for_confirmed_provenance():
    once = reconcile_earnings_events(
        [
            {
                "ticker": "AAPL",
                "event_date": date(2026, 7, 30),
                "provider": "finnhub",
            },
            {
                "ticker": "AAPL",
                "event_date": date(2026, 7, 30),
                "provider": "alpha_vantage_calendar",
            },
        ]
    )

    twice = reconcile_earnings_events(once)

    assert twice[0]["reconciliation_status"] == "confirmed"
    assert twice[0]["date_confidence"] == "high"
    assert twice[0]["observation_ids"] == once[0]["observation_ids"]


@patch("backend.src.collectors.earnings_collector.fetch_earnings_events")
def test_near_term_conflict_confirmation_is_bounded_and_keeps_disagreement_visible(
    mock_fetch,
):
    mock_fetch.return_value = [
        {
            "ticker": "AAPL",
            "event_date": date(2026, 9, 4),
            "provider": "yfinance",
            "provider_observation_id": "yfinance:AAPL:2026-09-04:unknown",
        }
    ]
    events = [
        {"ticker": "AAPL", "event_date": date(2026, 9, 3), "provider": "finnhub"},
        {
            "ticker": "AAPL",
            "event_date": date(2026, 9, 4),
            "provider": "alpha_vantage_calendar",
        },
        {"ticker": "MSFT", "event_date": date(2026, 9, 5), "provider": "finnhub"},
        {
            "ticker": "MSFT",
            "event_date": date(2026, 9, 6),
            "provider": "alpha_vantage_calendar",
        },
    ]

    attempts: dict[str, dict] = {}
    confirmed = confirm_near_term_earnings_conflicts(
        events,
        [{"ticker": "AAPL", "company_name": "Apple"}, {"ticker": "MSFT"}],
        as_of=date(2026, 9, 1),
        horizon_days=7,
        max_tickers=1,
        provider_attempts=attempts,
    )

    mock_fetch.assert_called_once_with(
        "AAPL",
        company_name="Apple",
        limit=32,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 8),
        provider_events=None,
        provider_attempts=attempts,
        fallback_to_alpha_vantage=False,
    )
    assert attempts["yfinance_conflict_confirmation"]["attempt_count"] == 1
    aapl = [event for event in confirmed if event["ticker"] == "AAPL"]
    assert {event["reconciliation_status"] for event in aapl} == {"conflicting"}
    assert {event["confirmation_status"] for event in aapl} == {
        "candidate_supported",
        "unresolved",
    }
    assert all(event["confirmation_providers"] == ["yfinance"] for event in aapl)
    assert not any("confirmation_status" in event for event in confirmed if event["ticker"] == "MSFT")


@patch("backend.src.collectors.earnings_collector.fetch_earnings_events")
def test_conflict_confirmation_skips_events_outside_seven_day_horizon(mock_fetch):
    events = [
        {"ticker": "ARQQ", "event_date": date(2026, 12, 7), "provider": "finnhub"},
        {
            "ticker": "ARQQ",
            "event_date": date(2026, 12, 9),
            "provider": "alpha_vantage_calendar",
        },
    ]

    confirmed = confirm_near_term_earnings_conflicts(
        events,
        [{"ticker": "ARQQ"}],
        as_of=date(2026, 9, 1),
    )

    mock_fetch.assert_not_called()
    assert {event["reconciliation_status"] for event in confirmed} == {"conflicting"}


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
    assert mock_get.call_count == 2
    near_params = mock_get.call_args_list[0].kwargs["params"]
    later_params = mock_get.call_args_list[1].kwargs["params"]
    assert near_params["from"] == "2026-06-29"
    assert near_params["to"] == "2026-07-06"
    assert later_params["from"] == "2026-07-07"
    assert later_params["to"] == "2026-07-13"
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

    assert mock_get.call_count == 2
    near_params = mock_get.call_args_list[0].kwargs["params"]
    later_params = mock_get.call_args_list[1].kwargs["params"]
    assert near_params["from"] == "2026-06-29"
    assert near_params["to"] == "2026-07-06"
    assert later_params["from"] == "2026-07-07"
    assert later_params["to"] == "2026-10-27"


@patch(
    "backend.src.collectors.earnings_collector.fetch_alpha_vantage_earnings_calendar_events"
)
@patch("backend.src.collectors.earnings_collector.requests.get")
def test_near_term_finnhub_query_confirms_representative_alpha_event(
    mock_get, mock_alpha, monkeypatch
):
    monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
    from backend.src.services.secrets import get_provider_api_key

    get_provider_api_key.cache_clear()
    mock_alpha.return_value = [
        {
            "ticker": "AVGO",
            "event_date": date(2026, 9, 2),
            "provider": "alpha_vantage_calendar",
            "provider_observation_id": (
                "alpha_vantage_calendar:AVGO:2026-09-02:2026-07-31"
            ),
        }
    ]
    near_response = MagicMock()
    near_response.raise_for_status.return_value = None
    near_response.json.return_value = {
        "earningsCalendar": [
            {"symbol": "AVGO", "date": "2026-09-02", "hour": "amc"}
        ]
    }
    later_response = MagicMock()
    later_response.raise_for_status.return_value = None
    later_response.json.return_value = {"earningsCalendar": []}
    mock_get.side_effect = [near_response, later_response]
    attempts: dict[str, dict] = {}

    events = fetch_earnings_calendar_events(
        [{"ticker": "AVGO", "company_name": "Broadcom"}],
        start_date=date(2026, 9, 2),
        end_date=date(2026, 12, 31),
        provider_attempts=attempts,
    )

    assert len(events) == 1
    assert events[0]["ticker"] == "AVGO"
    assert events[0]["reconciliation_status"] == "confirmed"
    assert events[0]["date_confidence"] == "high"
    assert len(events[0]["observation_ids"]) == 2
    assert attempts["finnhub"]["event_count"] == 1
    assert attempts["finnhub_long_range"]["event_count"] == 0


def test_select_stocks_honors_ticker_offset():
    stocks = [
        {"ticker": "MSFT"},
        {"ticker": "AAPL"},
        {"ticker": "NVDA"},
        {"ticker": "AMZN"},
    ]

    selected = _select_stocks(stocks, {"ticker_offset": 1, "max_tickers": 2})

    assert [stock["ticker"] for stock in selected] == ["AMZN", "MSFT"]


def test_rotating_fallback_wraps_without_favoring_alphabetical_prefix():
    stocks = [{"ticker": ticker} for ticker in ["AAPL", "MSFT", "NVDA", "TSLA"]]

    selected = _select_rotating_fallback_stocks(
        stocks,
        {"fallback_max_tickers": 3, "fallback_ticker_offset": 2},
        date(2026, 8, 1),
    )

    assert [stock["ticker"] for stock in selected] == ["NVDA", "TSLA", "AAPL"]


@patch("backend.src.collectors.earnings_collector.complete_persisted_manifest_task")
def test_degraded_empty_manifest_task_is_completed_as_failed(mock_complete):
    task_run = ManifestTaskRun(
        bucket="bucket",
        key="collection_manifest/2026-08-01.json",
        manifest_date=date(2026, 8, 1),
        task_id="earnings-1",
    )

    _complete_manifest_task_run(
        task_run,
        selected_ticker_count=10,
        stored_count=0,
        failed_tickers=[],
        provider_health={
            "status": "degraded",
            "reason": "provider_returned_zero_events",
        },
    )

    counts = mock_complete.call_args.args[2]
    assert counts.successful_tickers == 0
    assert counts.failed_tickers == 10
    assert mock_complete.call_args.kwargs == {
        "failed": True,
        "failure_reason": "provider_returned_zero_events",
    }


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


@patch("backend.src.collectors.earnings_collector.fetch_earnings_calendar_events")
@patch("backend.src.collectors.earnings_collector.DatabasePool")
@patch("backend.src.collectors.earnings_collector.store")
def test_handler_respects_explicit_max_tickers_for_earnings(
    mock_store,
    mock_pool,
    mock_fetch,
):
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "MSFT", "company_name": "Microsoft"},
        {"ticker": "AAPL", "company_name": "Apple"},
    ]
    mock_fetch.return_value = []

    result = handler({"max_tickers": 1, "fallback_max_tickers": 0}, None)

    assert result["statusCode"] == 200
    selected = mock_fetch.call_args.args[0]
    assert [stock["ticker"] for stock in selected] == ["AAPL"]


@patch("backend.src.collectors.earnings_collector.publish_calendar_provider_snapshots")
@patch("backend.src.collectors.earnings_collector.publish_calendar_artifacts")
@patch("backend.src.collectors.earnings_collector._emit_metric")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_calendar_events")
@patch("backend.src.collectors.earnings_collector.DatabasePool")
@patch("backend.src.collectors.earnings_collector.store")
def test_handler_scans_full_watchlist_and_marks_empty_calendar_degraded(
    mock_store,
    mock_pool,
    mock_fetch,
    mock_metric,
    mock_publish_artifacts,
    mock_publish_snapshots,
):
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "MSFT", "company_name": "Microsoft"},
        {"ticker": "AAPL", "company_name": "Apple"},
        {"ticker": "NVDA", "company_name": "NVIDIA"},
    ]
    mock_fetch.return_value = []

    result = handler(
        {"mode": "repair_calendars", "fallback_max_tickers": 0},
        None,
    )

    selected = mock_fetch.call_args.args[0]
    assert [stock["ticker"] for stock in selected] == ["AAPL", "MSFT", "NVDA"]
    assert result["body"]["status"] == "degraded"
    assert result["body"]["provider_health"]["reason"] == "provider_returned_zero_events"
    assert result["body"]["zero_event_tickers"] == ["AAPL", "MSFT", "NVDA"]
    assert mock_publish_artifacts.call_args.kwargs["collection_status"] == "degraded"
    mock_metric.assert_any_call("earnings_provider_degraded_runs", 1)


@patch("backend.src.collectors.earnings_collector.publish_calendar_provider_snapshots")
@patch("backend.src.collectors.earnings_collector.publish_calendar_artifacts")
@patch("backend.src.collectors.earnings_collector._emit_metric")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_events")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_calendar_events")
@patch("backend.src.collectors.earnings_collector.DatabasePool")
@patch("backend.src.collectors.earnings_collector.store")
def test_handler_uses_bounded_ticker_fallback_when_range_calendar_is_empty(
    mock_store,
    mock_pool,
    mock_fetch_calendar,
    mock_fetch_ticker,
    mock_metric,
    mock_publish_artifacts,
    mock_publish_snapshots,
):
    mock_store.active_stock_metadata.return_value = [
        {"ticker": "AAPL", "company_name": "Apple"},
        {"ticker": "MSFT", "company_name": "Microsoft"},
        {"ticker": "NVDA", "company_name": "NVIDIA"},
    ]
    mock_fetch_calendar.return_value = []
    mock_fetch_ticker.side_effect = lambda ticker, **kwargs: [
        {
            "ticker": ticker,
            "event_date": date(2026, 8, 20),
            "is_upcoming": True,
            "provider": "yfinance",
        }
    ]

    result = handler(
        {
            "mode": "repair_calendars",
            "fallback_max_tickers": 2,
            "fallback_ticker_offset": 1,
        },
        None,
    )

    assert mock_fetch_ticker.call_count == 2
    assert [call.args[0] for call in mock_fetch_ticker.call_args_list] == ["MSFT", "NVDA"]
    assert result["body"]["events_collected"] == 2
    assert result["body"]["status"] == "success"


@patch("backend.src.collectors.earnings_collector.publish_calendar_provider_snapshots")
@patch("backend.src.collectors.earnings_collector.publish_calendar_artifacts")
@patch("backend.src.collectors.earnings_collector.fetch_earnings_calendar_events")
@patch("backend.src.collectors.earnings_collector.DatabasePool")
@patch("backend.src.collectors.earnings_collector.store")
def test_handler_supports_repair_calendars_dry_run_for_earnings(
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
            "provider_budget": {"alpha_vantage": 3},
            "dry_run": True,
        },
        None,
    )

    assert result["statusCode"] == 200
    body = result["body"]
    assert body["status"] == "dry_run"
    assert body["mode"] == "repair_calendars"
    assert body["selected_tickers"] == ["AAPL"]
    assert body["provider_budget"] == {"alpha_vantage": 3}
    mock_fetch.assert_not_called()
    mock_store.put_earnings_event.assert_not_called()
    mock_publish_artifacts.assert_not_called()
    mock_publish_snapshots.assert_not_called()


@patch("backend.src.collectors.earnings_collector.complete_persisted_manifest_task")
@patch("backend.src.collectors.earnings_collector.mark_persisted_manifest_task_running")
@patch("backend.src.collectors.earnings_collector.get_persisted_manifest_task")
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
    mock_get_task,
    mock_mark_running,
    mock_complete_task,
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
    mock_get_task.return_value = task
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
    mock_mark_running.assert_called_once()
    mock_complete_task.assert_called_once()
    counts = mock_complete_task.call_args.args[2]
    assert counts.records_written == len(task.tickers)
    assert counts.successful_tickers == len(task.tickers)
    assert mock_publish_artifacts.call_args.kwargs["artifact_scope"] == task.task_id
    assert mock_publish_artifacts.call_args.kwargs["publish_latest"] is False
    assert mock_publish_snapshots.call_args.kwargs["artifact_scope"] == task.task_id
    assert mock_publish_snapshots.call_args.kwargs["publish_latest"] is False


@patch("backend.src.collectors.earnings_collector.complete_persisted_manifest_task")
@patch("backend.src.collectors.earnings_collector.mark_persisted_manifest_task_running")
@patch("backend.src.collectors.earnings_collector.get_persisted_manifest_task")
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
    mock_get_task,
    mock_mark_running,
    mock_complete_task,
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
    mock_get_task.return_value = task
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
    assert mock_complete_task.call_args.args[2].records_written == 2
