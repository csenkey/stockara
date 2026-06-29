"""Daily collection manifest distributor.

Creates the S3 manifest that coordinates bounded collector worker runs for the
active stock universe, then dispatches a small number of ready tasks per run.
"""

import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
import structlog

from src.db.connection import DatabasePool, store
from src.models.schemas import (
    CollectionCoverageGate,
    CollectionManifest,
    CollectionManifestSummary,
    CollectionTask,
    CollectionTaskStatus,
    CollectionTaskType,
    collection_manifest_s3_key,
)
from src.services.collection_manifest import emit_manifest_metrics, recompute_summary

logger = structlog.get_logger(__name__)

ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
PRICE_COLLECTOR_FUNCTION_NAME = os.environ.get("PRICE_COLLECTOR_FUNCTION_NAME", "")
NEWS_COLLECTOR_FUNCTION_NAME = os.environ.get("NEWS_COLLECTOR_FUNCTION_NAME", "")
EARNINGS_COLLECTOR_FUNCTION_NAME = os.environ.get("EARNINGS_COLLECTOR_FUNCTION_NAME", "")
DIVIDEND_COLLECTOR_FUNCTION_NAME = os.environ.get("DIVIDEND_COLLECTOR_FUNCTION_NAME", "")
PRICE_TASK_CHUNK_SIZE = int(os.environ.get("COLLECTION_PRICE_TASK_CHUNK_SIZE", "10"))
NEWS_TASK_CHUNK_SIZE = int(os.environ.get("COLLECTION_NEWS_TASK_CHUNK_SIZE", "50"))
CALENDAR_TASK_CHUNK_SIZE = int(
    os.environ.get("COLLECTION_CALENDAR_TASK_CHUNK_SIZE", "50")
)
MAX_TASKS_PER_RUN = int(os.environ.get("COLLECTION_MAX_TASKS_PER_RUN", "12"))
LEASE_MINUTES = int(os.environ.get("COLLECTION_TASK_LEASE_MINUTES", "15"))
ANALYSIS_HOUR_UTC = int(os.environ.get("COLLECTION_ANALYSIS_HOUR_UTC", "22"))


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Create or refresh today's daily collection manifest in S3."""
    event = event or {}
    log = logger.bind(lambda_event=event)
    manifest_date = _manifest_date(event)
    bucket = str(event.get("bucket") or ARTIFACT_BUCKET).strip()
    if not bucket:
        return _response(
            500,
            {
                "status": "failed",
                "message": "STOCKARA_ARTIFACT_BUCKET is not configured",
            },
        )

    key = collection_manifest_s3_key(manifest_date)
    now = datetime.now(timezone.utc)
    manifest = _load_existing_manifest(bucket, key)
    created = manifest is None
    if manifest is None:
        DatabasePool.initialize()
        try:
            stocks = store.active_stock_metadata()
        finally:
            DatabasePool.close()
        manifest = build_manifest(
            stocks,
            manifest_date=manifest_date,
            generated_at=now,
        )

    dispatched = _dispatch_ready_tasks(bucket, key, manifest, now)
    recompute_summary(manifest)
    if not dispatched:
        _put_manifest(bucket, key, manifest)
    log.info(
        "collection_manifest_refreshed",
        bucket=bucket,
        key=key,
        created=created,
        task_count=len(manifest.tasks),
        dispatched_task_count=len(dispatched),
        active_ticker_count=manifest.active_ticker_count,
    )
    return _response(
        200,
        {
            "status": "success",
            "bucket": bucket,
            "manifest_key": key,
            "manifest_date": manifest_date.isoformat(),
            "created": created,
            "task_count": len(manifest.tasks),
            "dispatched_task_count": len(dispatched),
            "dispatched_task_ids": dispatched,
            "active_ticker_count": manifest.active_ticker_count,
        },
    )


def build_manifest(
    stocks: list[dict[str, Any]],
    manifest_date: date,
    generated_at: datetime,
) -> CollectionManifest:
    """Build a daily manifest from active stock metadata."""
    active_tickers = sorted(
        {
            str(stock.get("ticker", "")).strip().upper()
            for stock in stocks
            if stock.get("ticker")
        }
    )
    tasks: list[CollectionTask] = []
    tasks.extend(
        _ticker_chunk_tasks(
            CollectionTaskType.PRICE,
            active_tickers,
            PRICE_TASK_CHUNK_SIZE,
            generated_at,
        )
    )
    tasks.extend(
        _ticker_chunk_tasks(
            CollectionTaskType.NEWS,
            active_tickers,
            NEWS_TASK_CHUNK_SIZE,
            generated_at,
        )
    )
    tasks.extend(
        _ticker_chunk_tasks(
            CollectionTaskType.EARNINGS,
            active_tickers,
            CALENDAR_TASK_CHUNK_SIZE,
            generated_at,
        )
    )
    tasks.extend(
        _ticker_chunk_tasks(
            CollectionTaskType.DIVIDEND,
            active_tickers,
            CALENDAR_TASK_CHUNK_SIZE,
            generated_at,
        )
    )
    analysis_not_before = generated_at.replace(
        hour=ANALYSIS_HOUR_UTC,
        minute=0,
        second=0,
        microsecond=0,
    )
    return CollectionManifest(
        manifest_date=manifest_date,
        generated_at=generated_at,
        updated_at=generated_at,
        analysis_not_before=analysis_not_before,
        active_ticker_count=len(active_tickers),
        task_types=[
            CollectionTaskType.PRICE,
            CollectionTaskType.NEWS,
            CollectionTaskType.EARNINGS,
            CollectionTaskType.DIVIDEND,
        ],
        tasks=tasks,
        summary=_initial_summary(tasks, len(active_tickers)),
    )


def _ticker_chunk_tasks(
    task_type: CollectionTaskType,
    tickers: list[str],
    chunk_size: int,
    created_at: datetime,
) -> list[CollectionTask]:
    tasks: list[CollectionTask] = []
    if not tickers:
        return tasks
    for index, start in enumerate(range(0, len(tickers), chunk_size)):
        chunk = tickers[start : start + chunk_size]
        tasks.append(
            CollectionTask(
                task_id=f"{task_type.value}-{index:04d}-{chunk[0]}-{chunk[-1]}",
                task_type=task_type,
                tickers=chunk,
                ticker_range_start=chunk[0],
                ticker_range_end=chunk[-1],
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return tasks


def _initial_summary(
    tasks: list[CollectionTask],
    active_ticker_count: int,
) -> CollectionManifestSummary:
    total_tasks = len(tasks)
    gates = [
        CollectionCoverageGate(
            name="price_freshness",
            passed=False,
            observed_value=Decimal("0"),
            required_value=Decimal("0.9"),
            unit="ratio",
            message="At least 90% of active tickers need fresh daily prices.",
        ),
        CollectionCoverageGate(
            name="news_freshness",
            passed=False,
            observed_value=Decimal("0"),
            required_value=Decimal("1"),
            unit="sources",
            message="All configured news sources must be available.",
        ),
        CollectionCoverageGate(
            name="calendar_coverage",
            passed=False,
            observed_value=Decimal("0"),
            required_value=Decimal("0.9"),
            unit="ratio",
            message="At least 90% of active tickers need calendar scan attempts.",
        ),
    ]
    return CollectionManifestSummary(
        total_tasks=total_tasks,
        pending_tasks=total_tasks,
        total_tickers=active_ticker_count,
        coverage_ratio=Decimal("0"),
        coverage_gates=gates,
    )


def _manifest_date(event: dict[str, Any]) -> date:
    value = event.get("manifest_date")
    if value:
        return date.fromisoformat(str(value))
    return datetime.now(timezone.utc).date()


def _load_existing_manifest(bucket: str, key: str) -> CollectionManifest | None:
    try:
        response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        payload = json.loads(response["Body"].read().decode("utf-8"))
        return CollectionManifest.model_validate(payload)
    except Exception:
        return None


def _dispatch_ready_tasks(
    bucket: str,
    key: str,
    manifest: CollectionManifest,
    now: datetime,
) -> list[str]:
    lambda_client = boto3.client("lambda")
    dispatched: list[str] = []
    for task in manifest.tasks:
        if len(dispatched) >= MAX_TASKS_PER_RUN:
            break
        if not _task_is_ready(task, now):
            continue
        function_name = _worker_function_name(task.task_type)
        if not function_name:
            task.status = CollectionTaskStatus.FAILED
            task.failure_reason = f"worker_not_configured:{task.task_type.value}"
            task.updated_at = now
            continue
        task.status = CollectionTaskStatus.LEASED
        task.lease_owner = "collection-distributor"
        task.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
        task.updated_at = now
        recompute_summary(manifest)
        _put_manifest(bucket, key, manifest)
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(
                {
                    "mode": "manifest_task",
                    "manifest_bucket": bucket,
                    "manifest_key": key,
                    "task_id": task.task_id,
                }
            ).encode("utf-8"),
        )
        dispatched.append(task.task_id)
    return dispatched


def _task_is_ready(task: CollectionTask, now: datetime) -> bool:
    if task.status == CollectionTaskStatus.PENDING:
        return True
    if task.status == CollectionTaskStatus.RETRY_WAIT:
        return task.next_retry_at is None or task.next_retry_at <= now
    if task.status in {CollectionTaskStatus.LEASED, CollectionTaskStatus.RUNNING}:
        return bool(task.lease_expires_at and task.lease_expires_at <= now)
    return False


def _worker_function_name(task_type: CollectionTaskType) -> str:
    return {
        CollectionTaskType.PRICE: PRICE_COLLECTOR_FUNCTION_NAME,
        CollectionTaskType.NEWS: NEWS_COLLECTOR_FUNCTION_NAME,
        CollectionTaskType.EARNINGS: EARNINGS_COLLECTOR_FUNCTION_NAME,
        CollectionTaskType.DIVIDEND: DIVIDEND_COLLECTOR_FUNCTION_NAME,
    }[task_type]


def _put_manifest(bucket: str, key: str, manifest: CollectionManifest) -> None:
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=manifest.model_dump_json(indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    emit_manifest_metrics(manifest)


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "body": body}
