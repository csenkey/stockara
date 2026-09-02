"""Tests for the resumable manual earnings-history backfill."""

from argparse import Namespace
from datetime import date
from unittest.mock import patch

from scripts.backfill_earnings_calendar_history import (
    backfill_earnings_calendar_history,
)


def _args(**overrides):
    values = {
        "tickers": None,
        "max_tickers": 2,
        "offset": 0,
        "limit": 32,
        "lookback_days": 1825,
        "lookahead_days": 120,
        "sleep": 0,
        "alpha_vantage_max_calls": 20,
        "dry_run": False,
        "publish_artifact": True,
    }
    values.update(overrides)
    return Namespace(**values)


@patch("src.services.static_artifacts.safe_publish_json_artifact")
@patch("src.services.earnings_history_coverage.publish_earnings_history_coverage")
@patch("src.services.calendar_artifacts.publish_calendar_artifacts")
@patch("src.collectors.earnings_collector._collect_per_ticker")
@patch("src.collectors.earnings_collector._reset_alpha_vantage_invocation_state")
@patch("src.db.connection.DatabasePool")
@patch("src.db.connection.store")
def test_backfill_records_provider_skip_and_publishes_resume_checkpoint(
    mock_store,
    mock_pool,
    mock_reset,
    mock_collect,
    mock_publish_calendar,
    mock_publish_coverage,
    mock_publish_json,
    monkeypatch,
):
    stocks = [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "NVDA"}]
    mock_store.active_stock_metadata.return_value = stocks

    def collect(selected, _event, _log, **kwargs):
        ticker = selected[0]["ticker"]
        if ticker == "AAPL":
            kwargs["ticker_collection_outcomes"][ticker] = "collected"
            return (
                [
                    {
                        "ticker": ticker,
                        "event_date": date(2025, 7, 30),
                        "reported_eps": 1,
                        "is_upcoming": False,
                    }
                ],
                [],
            )
        kwargs["ticker_collection_outcomes"][ticker] = "budget_exhausted"
        kwargs["provider_attempts"]["alpha_vantage"] = {
            "statuses": {"budget_exhausted": 1}
        }
        return [], [ticker]

    mock_collect.side_effect = collect
    mock_store.earnings_events_for_ticker.side_effect = lambda ticker, *_: (
        [
            {
                "ticker": ticker,
                "event_date": "2025-07-30",
                "reported_eps": 1,
            }
        ]
        if ticker == "AAPL"
        else []
    )
    monkeypatch.setenv("STOCKARA_ARTIFACT_BUCKET", "artifacts")

    summary = backfill_earnings_calendar_history(_args())

    assert summary["status"] == "incomplete"
    assert summary["successful_ticker_count"] == 1
    assert summary["provider_skipped_tickers"] == ["MSFT"]
    assert summary["resume_offset"] == 1
    assert summary["has_more"] is True
    mock_store.put_earnings_event.assert_called_once()
    mock_publish_calendar.assert_called_once()
    assert mock_publish_calendar.call_args.kwargs["publish_latest"] is False
    mock_publish_coverage.assert_called_once()
    assert [call.args[1] for call in mock_publish_json.call_args_list] == [
        "earnings/history-backfill/latest.json",
        "earnings/history-backfill/as_of_date="
        f"{date.today().isoformat()}/offset=0/checkpoint.json",
    ]
    mock_reset.assert_called_once_with({"alpha_vantage_max_calls": 20})
    mock_pool.close.assert_called_once()


@patch("src.services.static_artifacts.safe_publish_json_artifact")
@patch("src.services.earnings_history_coverage.publish_earnings_history_coverage")
@patch("src.services.calendar_artifacts.publish_calendar_artifacts")
@patch("src.collectors.earnings_collector._collect_per_ticker")
@patch("src.db.connection.DatabasePool")
@patch("src.db.connection.store")
def test_dry_run_never_writes_events_or_artifacts(
    mock_store,
    mock_pool,
    mock_collect,
    mock_publish_calendar,
    mock_publish_coverage,
    mock_publish_json,
):
    mock_store.active_stock_metadata.return_value = [{"ticker": "AAPL"}]

    def collect(selected, _event, _log, **kwargs):
        kwargs["ticker_collection_outcomes"][selected[0]["ticker"]] = "empty"
        return [], []

    mock_collect.side_effect = collect

    summary = backfill_earnings_calendar_history(
        _args(max_tickers=1, dry_run=True, publish_artifact=True)
    )

    assert summary["status"] == "incomplete"
    assert summary["incomplete_tickers"] == ["AAPL"]
    mock_store.put_earnings_event.assert_not_called()
    mock_publish_calendar.assert_not_called()
    mock_publish_coverage.assert_not_called()
    mock_publish_json.assert_not_called()
    mock_pool.close.assert_called_once()
