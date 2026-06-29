"""Tests for collection manifest update helpers."""

from datetime import date, datetime, timedelta, timezone

from src.collectors.collection_distributor import build_manifest
from src.models.schemas import (
    CollectionOutputCounts,
    CollectionTask,
    CollectionTaskStatus,
    CollectionTaskType,
)
from src.services.collection_manifest import (
    complete_task,
    emit_manifest_metrics,
    mark_task_running,
    retry_delay_for_failure,
)


def test_manifest_task_lifecycle_recomputes_summary():
    generated_at = datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        manifest_date=date(2026, 6, 20),
        generated_at=generated_at,
    )
    task = manifest.tasks[0]

    mark_task_running(manifest, task.task_id, lease_owner="request-1", now=generated_at)

    assert task.status == CollectionTaskStatus.RUNNING
    assert task.lease_owner == "request-1"
    assert task.attempts == 1
    assert manifest.summary.running_tasks == 1

    complete_task(
        manifest,
        task.task_id,
        CollectionOutputCounts(
            records_written=2,
            successful_tickers=2,
        ),
        now=generated_at,
    )

    assert task.status == CollectionTaskStatus.SUCCEEDED
    assert task.lease_owner is None
    assert manifest.summary.running_tasks == 0
    assert manifest.summary.succeeded_tasks == 1
    assert manifest.summary.successful_tickers == 2
    assert manifest.summary.coverage_ratio > 0


def test_failed_task_moves_to_retry_wait_before_max_attempts():
    generated_at = datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}],
        manifest_date=date(2026, 6, 20),
        generated_at=generated_at,
    )
    task = manifest.tasks[0]
    mark_task_running(manifest, task.task_id, now=generated_at)

    complete_task(
        manifest,
        task.task_id,
        CollectionOutputCounts(failed_tickers=1),
        failed=True,
        failure_reason="all_providers_failed",
        now=generated_at,
    )

    assert task.status == CollectionTaskStatus.RETRY_WAIT
    assert task.next_retry_at == generated_at + timedelta(minutes=30)
    assert manifest.summary.retry_wait_tasks == 1


def test_rate_limited_task_uses_provider_quota_delay():
    now = datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)
    task = CollectionTask(
        task_id="price-alpha-AAPL",
        task_type=CollectionTaskType.PRICE,
        provider="alpha_vantage",
        tickers=["AAPL"],
        created_at=now,
        updated_at=now,
        attempts=1,
    )

    assert retry_delay_for_failure(task, "rate limit exceeded") == timedelta(hours=24)


def test_provider_unsupported_failure_does_not_retry():
    generated_at = datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}],
        manifest_date=date(2026, 6, 20),
        generated_at=generated_at,
    )
    task = manifest.tasks[0]
    mark_task_running(manifest, task.task_id, now=generated_at)

    complete_task(
        manifest,
        task.task_id,
        CollectionOutputCounts(failed_tickers=1),
        failed=True,
        failure_reason="no_data",
        now=generated_at,
    )

    assert task.status == CollectionTaskStatus.FAILED
    assert task.next_retry_at is None
    assert manifest.summary.failed_tasks == 1


def test_coverage_gates_recompute_from_task_outputs():
    generated_at = datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        manifest_date=date(2026, 6, 20),
        generated_at=generated_at,
    )

    for task in manifest.tasks:
        mark_task_running(manifest, task.task_id, now=generated_at)
        complete_task(
            manifest,
            task.task_id,
            CollectionOutputCounts(
                records_written=len(task.tickers),
                successful_tickers=len(task.tickers),
            ),
            now=generated_at,
        )

    gates = {gate.name: gate for gate in manifest.summary.coverage_gates}
    assert gates["price_freshness"].passed is True
    assert gates["price_freshness"].observed_value == 1
    assert gates["news_freshness"].passed is True
    assert gates["news_freshness"].observed_value == 1
    assert gates["calendar_coverage"].passed is True
    assert gates["calendar_coverage"].observed_value == 1


def test_emit_manifest_metrics_reports_operational_health():
    generated_at = datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)
    manifest = build_manifest(
        [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        manifest_date=date(2026, 6, 20),
        generated_at=generated_at,
    )
    task = manifest.tasks[0]
    mark_task_running(manifest, task.task_id, now=generated_at)
    complete_task(
        manifest,
        task.task_id,
        CollectionOutputCounts(failed_tickers=2),
        failed=True,
        failure_reason="all_providers_failed",
        now=generated_at,
    )

    from unittest.mock import patch

    with patch("src.services.collection_manifest.boto3.client") as client:
        emit_manifest_metrics(manifest)

    cloudwatch = client.return_value
    metric_data = cloudwatch.put_metric_data.call_args.kwargs["MetricData"]
    metric_names = {metric["MetricName"] for metric in metric_data}
    assert "collection_manifest_age_minutes" in metric_names
    assert "collection_manifest_incomplete_tasks" in metric_names
    assert "collection_manifest_retry_wait_tasks" in metric_names
    assert "collection_manifest_low_coverage_gates" in metric_names
    assert "collection_provider_failure_tasks" in metric_names
