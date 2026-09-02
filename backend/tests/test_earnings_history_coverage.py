"""Tests for auditable historical earnings coverage artifacts."""

from datetime import date
from unittest.mock import patch

from backend.src.services.earnings_history_coverage import (
    build_earnings_history_coverage,
    publish_earnings_history_coverage,
)


def test_build_coverage_measures_each_ticker_and_field_completeness():
    events = [
        {
            "ticker": "AAPL",
            "event_date": date(2024, quarter * 3, 15),
            "fiscal_quarter": f"2024-Q{quarter}",
            "eps_estimate": 1,
            "reported_eps": 2,
        }
        for quarter in range(1, 5)
    ] + [
        {
            "ticker": "AAPL",
            "event_date": date(2025, quarter * 3, 15),
            "fiscal_quarter": f"2025-Q{quarter}",
            "eps_estimate": 1,
            "reported_eps": 2,
            "revenue_estimate": 3,
        }
        for quarter in range(1, 5)
    ]
    events.append(
        {
            "ticker": "MSFT",
            "event_date": "2025-07-20",
            "reported_eps": 1,
        }
    )
    events.append(
        {
            "ticker": "AAPL",
            "event_date": "2026-10-20",
            "reported_eps": None,
        }
    )

    payload = build_earnings_history_coverage(
        tickers=["msft", "NVDA", "AAPL"],
        events=events,
        as_of=date(2026, 9, 2),
    )

    assert payload["audit_status"] == "incomplete"
    assert payload["summary"] == {
        "complete_ticker_count": 1,
        "partial_ticker_count": 1,
        "missing_ticker_count": 1,
        "incomplete_ticker_count": 2,
        "incomplete_collection_ticker_count": 0,
        "coverage_percent": 33.33,
        "coverage_status_counts": {"complete": 1, "missing": 1, "partial": 1},
        "collection_outcome_counts": {"not_attempted": 3},
    }
    by_ticker = {row["ticker"]: row for row in payload["tickers"]}
    assert by_ticker["AAPL"]["distinct_quarter_count"] == 8
    assert by_ticker["AAPL"]["reported_eps_count"] == 8
    assert by_ticker["AAPL"]["revenue_estimate_count"] == 4
    assert by_ticker["AAPL"]["coverage_status"] == "complete"
    assert by_ticker["MSFT"]["coverage_status"] == "partial"
    assert by_ticker["NVDA"]["coverage_status"] == "missing"


def test_budget_and_quota_skips_are_explicitly_incomplete():
    payload = build_earnings_history_coverage(
        tickers=["AAPL", "MSFT"],
        events=[],
        as_of=date(2026, 9, 2),
        collection_outcomes={
            "AAPL": "budget_exhausted",
            "MSFT": "rate_limited",
        },
    )

    assert payload["audit_status"] == "incomplete"
    assert payload["summary"]["incomplete_collection_ticker_count"] == 2
    by_ticker = {row["ticker"]: row for row in payload["tickers"]}
    assert by_ticker["AAPL"]["incomplete_reasons"] == [
        "insufficient_quarters",
        "collection_budget_exhausted",
    ]
    assert by_ticker["MSFT"]["incomplete_reasons"] == [
        "insufficient_quarters",
        "collection_rate_limited",
    ]


@patch("backend.src.services.earnings_history_coverage.publish_json_artifact")
def test_publish_coverage_writes_dated_scoped_artifact_without_latest(mock_publish):
    payload = {"as_of_date": "2026-09-02"}

    publish_earnings_history_coverage(
        bucket="artifacts",
        payload=payload,
        artifact_scope="earnings/0001",
        publish_latest=False,
    )

    mock_publish.assert_called_once_with(
        "artifacts",
        "earnings/history-coverage/as_of_date=2026-09-02/"
        "task_id=earnings-0001/coverage.json",
        payload,
    )


@patch("backend.src.services.earnings_history_coverage.publish_json_artifact")
def test_publish_full_coverage_also_updates_latest(mock_publish):
    payload = {"as_of_date": "2026-09-02"}

    publish_earnings_history_coverage(bucket="artifacts", payload=payload)

    assert [call.args[1] for call in mock_publish.call_args_list] == [
        "earnings/history-coverage/as_of_date=2026-09-02/coverage.json",
        "earnings/history-coverage/latest.json",
    ]
