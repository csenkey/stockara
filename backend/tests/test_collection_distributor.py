"""Tests for the collection distributor manifest creation."""

import json
from datetime import date, datetime, timedelta, timezone
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
    expected_manifest = build_manifest(
        [{"ticker": "aapl"}, {"ticker": "msft"}],
        manifest_date=date(2026, 6, 20),
        generated_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
    )
    tasks_by_id = {task.task_id: task for task in expected_manifest.tasks}

    def lease_task(_, task_id, **kwargs):
        task = tasks_by_id[task_id].model_copy(deep=True)
        task.status = CollectionTaskStatus.LEASED
        task.lease_owner = kwargs["lease_owner"]
        task.lease_expires_at = kwargs["lease_expires_at"]
        return task

    with (
        patch(
            "src.collectors.collection_distributor.refresh_manifest_task_state",
            side_effect=lambda manifest: manifest,
        ),
        patch(
            "src.collectors.collection_distributor.lease_persisted_manifest_task",
            side_effect=lease_task,
        ),
        patch(
            "src.collectors.collection_distributor.PRICE_COLLECTOR_FUNCTION_NAME",
            "stockara-stock-collector",
        ),
        patch(
            "src.collectors.collection_distributor.NEWS_COLLECTOR_FUNCTION_NAME",
            "stockara-news-collector",
        ),
        patch(
            "src.collectors.collection_distributor.EARNINGS_COLLECTOR_FUNCTION_NAME",
            "stockara-earnings-collector",
        ),
        patch(
            "src.collectors.collection_distributor.DIVIDEND_COLLECTOR_FUNCTION_NAME",
            "stockara-dividend-collector",
        ),
    ):
        response = handler(
            {
                "bucket": "stockara-artifacts",
                "manifest_date": "2026-06-20",
                "max_tasks_per_run": 2,
            },
            None,
        )

    assert response["statusCode"] == 200
    assert response["body"]["manifest_key"] == "collection_manifest/2026-06-20.json"
    assert response["body"]["task_count"] == 4
    assert response["body"]["dispatched_task_count"] == 2
    assert response["body"]["max_tasks_per_run"] == 2
    assert response["body"]["task_counts"]["leased"] == 2
    assert response["body"]["task_counts"]["pending"] == 2
    assert response["body"]["ready_task_count"] == 2
    assert response["body"]["active_incomplete_task_count"] == 4
    assert response["body"]["incomplete_task_count"] == 4
    assert response["body"]["dispatch_complete"] is False
    assert response["body"]["dispatch_ready_for_analysis"] is False
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
    assert health_payload["tasks_by_type"]["news"]["leased"] == 1
    assert lambda_client.invoke.call_count == 2


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
    tasks_by_id = {task.task_id: task for task in manifest.tasks}

    def lease_task(_, task_id, **kwargs):
        task = tasks_by_id[task_id].model_copy(deep=True)
        task.status = CollectionTaskStatus.LEASED
        task.lease_owner = kwargs["lease_owner"]
        task.lease_expires_at = kwargs["lease_expires_at"]
        return task

    with patch(
        "src.collectors.collection_distributor.lease_persisted_manifest_task",
        side_effect=lease_task,
    ):
        dispatched = collection_distributor._dispatch_ready_tasks(
            "stockara-artifacts",
            "collection_manifest/2026-06-20.json",
            manifest,
            datetime(2026, 6, 20, 7, 31, tzinfo=timezone.utc),
            max_tasks_per_run=4,
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


def test_production_sized_manifest_uses_bounded_chunk_tasks():
    generated_at = datetime(2026, 7, 31, 21, 5, tzinfo=timezone.utc)
    stocks = [{"ticker": f"T{index:04d}"} for index in range(900)]

    with (
        patch.object(collection_distributor, "PRICE_TASK_CHUNK_SIZE", 10),
        patch.object(collection_distributor, "NEWS_TASK_CHUNK_SIZE", 50),
        patch.object(collection_distributor, "CALENDAR_TASK_CHUNK_SIZE", 10),
    ):
        manifest = build_manifest(
            stocks,
            manifest_date=date(2026, 7, 31),
            generated_at=generated_at,
        )

    assert manifest.summary.total_tasks == 288
    assert sum(
        task.task_type == CollectionTaskType.PRICE for task in manifest.tasks
    ) == 90
    assert sum(
        task.task_type == CollectionTaskType.NEWS for task in manifest.tasks
    ) == 18
    assert sum(
        task.task_type == CollectionTaskType.EARNINGS for task in manifest.tasks
    ) == 90
    assert sum(
        task.task_type == CollectionTaskType.DIVIDEND for task in manifest.tasks
    ) == 90


def test_dispatch_deadline_exits_with_active_tasks(monkeypatch):
    generated_at = datetime(2026, 7, 31, 21, 5, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}],
        manifest_date=date(2026, 7, 31),
        generated_at=generated_at,
    )
    monkeypatch.setattr(collection_distributor, "DISPATCH_DEADLINE_MINUTES", 70)

    before = collection_distributor._manifest_dispatch_status(
        manifest,
        generated_at + timedelta(minutes=69),
    )
    after = collection_distributor._manifest_dispatch_status(
        manifest,
        generated_at + timedelta(minutes=70),
    )

    assert before["dispatch_deadline_exceeded"] is False
    assert after["dispatch_deadline_exceeded"] is True
    assert after["dispatch_ready_for_analysis"] is False
    assert after["analysis_not_before"] == "2026-07-31T22:00:00+00:00"


def test_expired_lease_is_ready_for_redispatch():
    generated_at = datetime(2026, 7, 31, 21, 5, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}],
        manifest_date=date(2026, 7, 31),
        generated_at=generated_at,
    )
    task = manifest.tasks[0]
    task.status = CollectionTaskStatus.LEASED
    task.lease_expires_at = generated_at + timedelta(minutes=15)

    assert collection_distributor._task_is_ready(
        task,
        generated_at + timedelta(minutes=14),
    ) is False
    assert collection_distributor._task_is_ready(
        task,
        generated_at + timedelta(minutes=15),
    ) is True


@patch("src.collectors.collection_distributor.DatabasePool")
def test_handler_fails_without_artifact_bucket(mock_pool, monkeypatch):
    monkeypatch.setattr(
        "src.collectors.collection_distributor.ARTIFACT_BUCKET",
        "",
    )

    response = handler({"manifest_date": "2026-06-20"}, None)

    assert response["statusCode"] == 500
    assert response["body"]["status"] == "failed"
