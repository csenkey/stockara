"""Tests for missing stock price gap detection."""

from datetime import date, datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch

from src.collectors.collection_distributor import build_manifest
from src.collectors.stock_gap_scanner import (
    _date_ranges,
    _gap_tasks_for_stocks,
    _trading_days,
    handler,
)
from src.models.schemas import CollectionTaskType


def test_trading_days_skip_weekends_and_market_holidays():
    days = _trading_days(date(2026, 6, 18), date(2026, 6, 22))

    assert days == [date(2026, 6, 18), date(2026, 6, 22)]


def test_date_ranges_group_adjacent_trading_sessions():
    ranges = _date_ranges(
        [
            date(2026, 6, 18),
            date(2026, 6, 22),
            date(2026, 6, 24),
        ]
    )

    assert ranges == [
        (date(2026, 6, 18), date(2026, 6, 22)),
        (date(2026, 6, 24), date(2026, 6, 24)),
    ]


@patch("src.collectors.stock_gap_scanner.store.get_stock_data")
def test_gap_tasks_for_stocks_create_missing_range_tasks(mock_get_stock_data):
    mock_get_stock_data.return_value = [
        {"ticker": "AAPL", "trading_date": "2026-06-15"},
        {"ticker": "AAPL", "trading_date": "2026-06-18"},
    ]

    tasks = _gap_tasks_for_stocks(
        [{"ticker": "AAPL"}],
        date(2026, 6, 15),
        date(2026, 6, 18),
        datetime(2026, 6, 19, tzinfo=timezone.utc),
        set(),
        10,
    )

    assert [task.task_id for task in tasks] == [
        "price-backfill-AAPL-2026-06-16-2026-06-17"
    ]
    assert tasks[0].task_type == CollectionTaskType.PRICE
    assert tasks[0].tickers == ["AAPL"]
    assert tasks[0].start_date == date(2026, 6, 16)
    assert tasks[0].end_date == date(2026, 6, 17)
    assert tasks[0].reason == "missing_stock_data_gap"


@patch("src.collectors.stock_gap_scanner._emit_metric")
@patch("src.collectors.stock_gap_scanner.boto3.client")
@patch("src.collectors.stock_gap_scanner.store.get_stock_data")
@patch("src.collectors.stock_gap_scanner.store.active_stock_metadata")
@patch("src.collectors.stock_gap_scanner.DatabasePool")
def test_handler_appends_gap_tasks_to_existing_manifest(
    mock_pool,
    mock_active_stocks,
    mock_get_stock_data,
    mock_boto_client,
    mock_metric,
):
    generated_at = datetime(2026, 6, 19, 7, 30, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}],
        manifest_date=date(2026, 6, 19),
        generated_at=generated_at,
    )
    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": BytesIO(manifest.model_dump_json().encode("utf-8"))
    }
    mock_boto_client.side_effect = lambda service: {
        "s3": s3,
        "cloudwatch": MagicMock(),
    }[service]
    mock_active_stocks.return_value = [{"ticker": "AAPL"}]
    mock_get_stock_data.return_value = [
        {"ticker": "AAPL", "trading_date": "2026-06-15"},
        {"ticker": "AAPL", "trading_date": "2026-06-17"},
    ]

    response = handler(
        {
            "bucket": "stockara-artifacts",
            "manifest_date": "2026-06-19",
            "scan_start_date": "2026-06-15",
            "scan_end_date": "2026-06-17",
        },
        None,
    )

    assert response["statusCode"] == 200
    assert response["body"]["tasks_created"] == 1
    put_call = s3.put_object.call_args.kwargs
    assert put_call["Key"] == "collection_manifest/2026-06-19.json"
    payload = put_call["Body"].decode("utf-8")
    assert "price-backfill-AAPL-2026-06-16-2026-06-16" in payload
    mock_metric.assert_called_once_with("stock_price_gaps_detected", 1)
