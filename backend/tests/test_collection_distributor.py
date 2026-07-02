"""Tests for the collection distributor manifest creation."""

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from src.collectors import collection_distributor
from src.collectors.collection_distributor import build_manifest, handler
from src.models.schemas import CollectionTaskStatus, CollectionTaskType


def test_build_manifest_chunks_all_task_types():
    stocks = [{"ticker": f"T{index:03d}"} for index in range(1, 26)]
    generated_at = datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)

    manifest = build_manifest(
        stocks,
        manifest_date=date(2026, 6, 20),
        generated_at=generated_at,
    )

    price_tasks = [
        task for task in manifest.tasks if task.task_type == CollectionTaskType.PRICE
    ]
    news_tasks = [
        task for task in manifest.tasks if task.task_type == CollectionTaskType.NEWS
    ]
    earnings_tasks = [
        task for task in manifest.tasks if task.task_type == CollectionTaskType.EARNINGS
    ]
    dividend_tasks = [
        task for task in manifest.tasks if task.task_type == CollectionTaskType.DIVIDEND
    ]

    assert manifest.s3_key == "collection_manifest/2026-06-20.json"
    assert manifest.active_ticker_count == 25
    assert len(price_tasks) == 3
    assert len(news_tasks) == 1
    assert len(earnings_tasks) == 1
    assert len(dividend_tasks) == 1
    assert price_tasks[0].tickers == [
        "T001",
        "T002",
        "T003",
        "T004",
        "T005",
        "T006",
        "T007",
        "T008",
        "T009",
        "T010",
    ]
    assert manifest.summary.total_tasks == len(manifest.tasks)
    assert manifest.summary.pending_tasks == len(manifest.tasks)


@patch("src.collectors.collection_distributor.boto3.client")
@patch("src.collectors.collection_distributor.store.active_stock_metadata")
@patch("src.collectors.collection_distributor.DatabasePool")
def test_handler_writes_manifest_to_s3(mock_pool, mock_active_stocks, mock_boto_client):
    s3 = MagicMock()
    lambda_client = MagicMock()
    mock_boto_client.side_effect = lambda service: {
        "s3": s3,
        "lambda": lambda_client,
        "cloudwatch": MagicMock(),
    }[service]
    mock_active_stocks.return_value = [{"ticker": "aapl"}, {"ticker": "msft"}]

    with patch(
        "src.collectors.collection_distributor.PRICE_COLLECTOR_FUNCTION_NAME",
        "stockara-stock-collector",
    ):
        response = handler(
            {
                "bucket": "stockara-artifacts",
                "manifest_date": "2026-06-20",
            },
            None,
        )

    assert response["statusCode"] == 200
    assert response["body"]["manifest_key"] == "collection_manifest/2026-06-20.json"
    assert response["body"]["task_count"] == 4
    assert response["body"]["dispatched_task_count"] == 1
    assert s3.put_object.called
    call = next(
        item.kwargs
        for item in s3.put_object.call_args_list
        if item.kwargs["Key"] == "collection_manifest/2026-06-20.json"
    )
    assert call["Bucket"] == "stockara-artifacts"
    assert call["Key"] == "collection_manifest/2026-06-20.json"
    payload = json.loads(call["Body"].decode("utf-8"))
    assert payload["active_ticker_count"] == 2
    assert payload["tasks"][0]["tickers"] == ["AAPL", "MSFT"]
    assert payload["tasks"][0]["status"] == CollectionTaskStatus.LEASED
    health_call = next(
        item.kwargs
        for item in s3.put_object.call_args_list
        if item.kwargs["Key"] == "data-health/latest.json"
    )
    health_payload = json.loads(health_call["Body"].decode("utf-8"))
    assert health_payload["manifest_key"] == "collection_manifest/2026-06-20.json"
    assert health_payload["task_counts"]["total"] == 4
    assert health_payload["tasks_by_type"]["price"]["leased"] == 1
    lambda_client.invoke.assert_called_once()


@patch("src.collectors.collection_distributor.boto3.client")
def test_dispatch_ready_tasks_round_robins_task_types(mock_boto_client, monkeypatch):
    s3 = MagicMock()
    lambda_client = MagicMock()
    mock_boto_client.side_effect = lambda service: {
        "s3": s3,
        "lambda": lambda_client,
        "cloudwatch": MagicMock(),
    }[service]
    monkeypatch.setattr(collection_distributor, "PRICE_COLLECTOR_FUNCTION_NAME", "price-fn")
    monkeypatch.setattr(collection_distributor, "NEWS_COLLECTOR_FUNCTION_NAME", "news-fn")
    monkeypatch.setattr(collection_distributor, "EARNINGS_COLLECTOR_FUNCTION_NAME", "earnings-fn")
    monkeypatch.setattr(collection_distributor, "DIVIDEND_COLLECTOR_FUNCTION_NAME", "dividend-fn")
    monkeypatch.setattr(collection_distributor, "MAX_TASKS_PER_RUN", 4)
    manifest = build_manifest(
        [{"ticker": f"T{index:03d}"} for index in range(1, 26)],
        manifest_date=date(2026, 6, 20),
        generated_at=datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc),
    )

    dispatched = collection_distributor._dispatch_ready_tasks(
        "stockara-artifacts",
        "collection_manifest/2026-06-20.json",
        manifest,
        datetime(2026, 6, 20, 7, 31, tzinfo=timezone.utc),
    )

    assert dispatched == [
        "price-0000-T001-T010",
        "news-0000-T001-T025",
        "earnings-0000-T001-T025",
        "dividend-0000-T001-T025",
    ]
    invoked_functions = [
        call.kwargs["FunctionName"] for call in lambda_client.invoke.call_args_list
    ]
    assert invoked_functions == ["price-fn", "news-fn", "earnings-fn", "dividend-fn"]


@patch("src.collectors.collection_distributor.DatabasePool")
def test_handler_fails_without_artifact_bucket(mock_pool, monkeypatch):
    monkeypatch.setattr(
        "src.collectors.collection_distributor.ARTIFACT_BUCKET",
        "",
    )

    response = handler({"manifest_date": "2026-06-20"}, None)

    assert response["statusCode"] == 500
    assert response["body"]["status"] == "failed"
