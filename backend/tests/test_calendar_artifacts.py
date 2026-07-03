"""Tests for calendar S3 artifact publication."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from backend.src.services.calendar_artifacts import (
    publish_calendar_artifacts,
    publish_calendar_provider_snapshots,
)


@patch("backend.src.services.calendar_artifacts.publish_json_artifact")
def test_publish_calendar_artifacts_writes_latest_collection_and_ticker_views(mock_publish):
    publish_calendar_artifacts(
        bucket="artifact-bucket",
        event_type="earnings",
        collection_date=date(2026, 7, 3),
        range_start=date(2021, 7, 4),
        range_end=date(2026, 10, 31),
        selected_tickers=["AAPL", "MSFT"],
        events=[
            {
                "ticker": "AAPL",
                "event_date": date(2026, 7, 30),
                "eps_estimate": Decimal("2.15"),
                "provider": "finnhub",
            },
            {
                "ticker": "MSFT",
                "event_date": date(2026, 7, 28),
                "eps_estimate": Decimal("3.02"),
                "provider": "yfinance",
            },
        ],
    )

    keys = [call.args[1] for call in mock_publish.call_args_list]
    assert "calendar/normalized/earnings/collection_date=2026-07-03/events.json" in keys
    assert "calendar/normalized/earnings/latest.json" in keys
    assert "calendar/by-ticker/AAPL/earnings.json" in keys
    assert "calendar/by-ticker/MSFT/earnings.json" in keys

    latest_payload = next(
        call.args[2]
        for call in mock_publish.call_args_list
        if call.args[1] == "calendar/normalized/earnings/latest.json"
    )
    assert latest_payload["range_start"] == "2021-07-04"
    assert latest_payload["range_end"] == "2026-10-31"
    assert latest_payload["event_count"] == 2
    assert latest_payload["events"][0]["event_date"] == "2026-07-30"


@patch("backend.src.services.calendar_artifacts.publish_json_artifact")
def test_publish_calendar_provider_snapshots_writes_raw_provider_views(mock_publish):
    publish_calendar_provider_snapshots(
        bucket="artifact-bucket",
        event_type="earnings",
        collection_date=date(2026, 7, 3),
        range_start=date(2021, 7, 4),
        range_end=date(2026, 10, 31),
        selected_tickers=["AAPL"],
        provider_events=[
            {
                "provider": "finnhub",
                "ticker": "AAPL",
                "event_date": date(2026, 7, 30),
                "raw_fields": {"epsEstimate": Decimal("2.15")},
            }
        ],
    )

    keys = [call.args[1] for call in mock_publish.call_args_list]
    assert "calendar/raw/finnhub/earnings/collection_date=2026-07-03/events.json" in keys
    assert "calendar/raw/finnhub/earnings/latest.json" in keys

    latest_payload = next(
        call.args[2]
        for call in mock_publish.call_args_list
        if call.args[1] == "calendar/raw/finnhub/earnings/latest.json"
    )
    assert latest_payload["raw_event_count"] == 1
    assert latest_payload["raw_events"][0]["event_date"] == "2026-07-30"
    assert latest_payload["raw_events"][0]["raw_fields"]["epsEstimate"] == 2.15
