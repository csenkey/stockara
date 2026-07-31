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
from src.services.collection_manifest import (
    lease_persisted_manifest_task,
    refresh_manifest_task_state,
)
from src.services.static_artifacts import safe_publish_json_artifact

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
DISPATCH_DEADLINE_MINUTES = int(
    os.environ.get("COLLECTION_DISPATCH_DEADLINE_MINUTES", "70")
)


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
    task_types = _task_types(event)
    workflow_started_at = _workflow_started_at(event, now)
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
            task_types=task_types,
        )
    refresh_manifest_task_state(manifest)
    if task_types is not None:
        manifest.tasks = [
            task for task in manifest.tasks if task.task_type in task_types
        ]
        manifest.task_types = task_types
    manifest.analysis_not_before = workflow_started_at
    recompute_summary(manifest)

    max_tasks_per_run = _max_tasks_per_run(event)
    dispatched = _dispatch_ready_tasks(bucket, key, manifest, now, max_tasks_per_run)
    recompute_summary(manifest)
    _put_manifest(bucket, key, manifest)
    _publish_data_health_artifact(bucket, key, manifest, now)
    dispatch_status = _manifest_dispatch_status(
        manifest,
        now,
        dispatch_started_at=workflow_started_at,
    )
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
            "max_tasks_per_run": max_tasks_per_run,
            "active_ticker_count": manifest.active_ticker_count,
            **dispatch_status,
        },
    )


def build_manifest(
    stocks: list[dict[str, Any]],
    manifest_date: date,
    generated_at: datetime,
    task_types: list[CollectionTaskType] | None = None,
) -> CollectionManifest:
    """Build a daily manifest from active stock metadata."""
    task_types = task_types or [
        CollectionTaskType.PRICE,
        CollectionTaskType.NEWS,
        CollectionTaskType.EARNINGS,
        CollectionTaskType.DIVIDEND,
    ]
    active_tickers = sorted(
        {
            str(stock.get("ticker", "")).strip().upper()
            for stock in stocks
            if stock.get("ticker")
        }
    )
    tasks: list[CollectionTask] = []
    task_chunk_sizes = {
        CollectionTaskType.PRICE: PRICE_TASK_CHUNK_SIZE,
        CollectionTaskType.NEWS: NEWS_TASK_CHUNK_SIZE,
        CollectionTaskType.EARNINGS: CALENDAR_TASK_CHUNK_SIZE,
        CollectionTaskType.DIVIDEND: CALENDAR_TASK_CHUNK_SIZE,
    }
    for task_type in task_types:
        tasks.extend(
            _ticker_chunk_tasks(
                task_type,
                active_tickers,
                task_chunk_sizes[task_type],
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
        task_types=task_types,
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
            message=(
                "Share of active tickers with completed fresh price collection. "
                "Tickers with stale or missing prices are excluded individually."
            ),
        ),
        CollectionCoverageGate(
            name="news_freshness",
            passed=False,
            observed_value=Decimal("0"),
            required_value=Decimal("1"),
            unit="ratio",
            message=(
                "Share of manifest news chunks completed. Manual news collection can "
                "refresh news/latest.json without completing every manifest chunk."
            ),
        ),
        CollectionCoverageGate(
            name="calendar_coverage",
            passed=False,
            observed_value=Decimal("0"),
            required_value=Decimal("0.9"),
            unit="ratio",
            message=(
                "Share of active tickers with calendar scan attempts. Missing upcoming "
                "earnings or dividend events are context gaps, not candidate blockers."
            ),
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


def _max_tasks_per_run(event: dict[str, Any]) -> int:
    value = event.get("max_tasks_per_run")
    if value is None:
        return MAX_TASKS_PER_RUN
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return MAX_TASKS_PER_RUN


def _task_types(event: dict[str, Any]) -> list[CollectionTaskType] | None:
    values = event.get("task_types")
    if values is None:
        return None
    return list(dict.fromkeys(CollectionTaskType(str(value)) for value in values))


def _workflow_started_at(
    event: dict[str, Any],
    fallback: datetime,
) -> datetime:
    value = event.get("workflow_started_at")
    if not value:
        return fallback
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


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
    max_tasks_per_run: int,
) -> list[str]:
    lambda_client = boto3.client("lambda")
    dispatched: list[str] = []
    active_task_count = sum(
        task.status in {CollectionTaskStatus.LEASED, CollectionTaskStatus.RUNNING}
        and bool(task.lease_expires_at and task.lease_expires_at > now)
        for task in manifest.tasks
    )
    available_slots = max(max_tasks_per_run - active_task_count, 0)
    for task in _ready_tasks_by_type(manifest, now):
        if len(dispatched) >= available_slots:
            break
        function_name = _worker_function_name(task.task_type)
        if not function_name:
            task.status = CollectionTaskStatus.FAILED
            task.failure_reason = f"worker_not_configured:{task.task_type.value}"
            task.updated_at = now
            continue
        leased_task = lease_persisted_manifest_task(
            manifest.manifest_date,
            task.task_id,
            lease_owner="collection-distributor",
            lease_expires_at=now + timedelta(minutes=LEASE_MINUTES),
            now=now,
        )
        if leased_task is None:
            continue
        _replace_manifest_task(manifest, leased_task)
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(
                {
                    "mode": "manifest_task",
                    "manifest_bucket": bucket,
                    "manifest_key": key,
                    "manifest_date": manifest.manifest_date.isoformat(),
                    "task_id": task.task_id,
                }
            ).encode("utf-8"),
        )
        dispatched.append(task.task_id)
    return dispatched


def _replace_manifest_task(
    manifest: CollectionManifest,
    replacement: CollectionTask,
) -> None:
    for index, task in enumerate(manifest.tasks):
        if task.task_id == replacement.task_id:
            manifest.tasks[index] = replacement
            return
    raise ValueError(f"Collection manifest task not found: {replacement.task_id}")


def _manifest_dispatch_status(
    manifest: CollectionManifest,
    now: datetime,
    *,
    dispatch_started_at: datetime | None = None,
) -> dict[str, Any]:
    summary = manifest.summary
    pending = summary.pending_tasks
    leased = summary.leased_tasks
    running = summary.running_tasks
    retry_wait = summary.retry_wait_tasks
    failed = summary.failed_tasks
    active_incomplete = pending + leased + running
    incomplete = active_incomplete + retry_wait
    dispatch_deadline = (dispatch_started_at or manifest.generated_at) + timedelta(
        minutes=DISPATCH_DEADLINE_MINUTES
    )
    return {
        "task_counts": {
            "total": summary.total_tasks,
            "pending": pending,
            "leased": leased,
            "running": running,
            "succeeded": summary.succeeded_tasks,
            "failed": failed,
            "retry_wait": retry_wait,
            "skipped": summary.skipped_tasks,
        },
        "ready_task_count": len(_ready_tasks_by_type(manifest, now)),
        "active_incomplete_task_count": active_incomplete,
        "incomplete_task_count": incomplete,
        "terminal_failed_task_count": failed,
        "dispatch_complete": incomplete == 0,
        "dispatch_ready_for_analysis": active_incomplete == 0,
        "dispatch_deadline": dispatch_deadline.isoformat(),
        "dispatch_deadline_exceeded": (
            active_incomplete > 0 and now >= dispatch_deadline
        ),
        "analysis_not_before": (
            manifest.analysis_not_before.isoformat()
            if manifest.analysis_not_before
            else None
        ),
    }


def _ready_tasks_by_type(
    manifest: CollectionManifest,
    now: datetime,
) -> list[CollectionTask]:
    """Return ready tasks in a fair task-type round-robin order.

    Manifests contain all price chunks first, followed by news, earnings, and
    dividend chunks. Walking the raw manifest order can starve the later task
    types when only a small number of tasks is dispatched each run.
    """
    grouped: dict[CollectionTaskType, list[CollectionTask]] = {
        task_type: [] for task_type in manifest.task_types
    }
    for task in manifest.tasks:
        if _task_is_ready(task, now):
            grouped.setdefault(task.task_type, []).append(task)

    ordered: list[CollectionTask] = []
    while True:
        added = False
        for task_type in manifest.task_types:
            tasks = grouped.get(task_type) or []
            if tasks:
                ordered.append(tasks.pop(0))
                added = True
        if not added:
            break
    return ordered


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


def _publish_data_health_artifact(
    bucket: str,
    manifest_key: str,
    manifest: CollectionManifest,
    generated_at: datetime,
) -> None:
    summary = manifest.summary.model_dump(mode="json") if manifest.summary else {}
    payload = {
        "generated_at": generated_at.isoformat(),
        "manifest_date": manifest.manifest_date.isoformat(),
        "manifest_key": manifest_key,
        "active_ticker_count": manifest.active_ticker_count,
        "analysis_not_before": (
            manifest.analysis_not_before.isoformat()
            if manifest.analysis_not_before
            else None
        ),
        "task_counts": {
            "total": summary.get("total_tasks", 0),
            "pending": summary.get("pending_tasks", 0),
            "leased": summary.get("leased_tasks", 0),
            "running": summary.get("running_tasks", 0),
            "succeeded": summary.get("succeeded_tasks", 0),
            "failed": summary.get("failed_tasks", 0),
            "retry_wait": summary.get("retry_wait_tasks", 0),
            "retry_exhausted": summary.get("retry_exhausted_tasks", 0),
        },
        "output_counts": summary.get("output_counts", {}),
        "coverage_ratio": summary.get("coverage_ratio", 0),
        "coverage_gates": summary.get("coverage_gates", []),
        "tasks_by_type": _task_counts_by_type(manifest),
        "failed_tasks": _task_rows(manifest, failed_only=True),
        "recent_tasks": _task_rows(manifest, failed_only=False)[:50],
    }
    safe_publish_json_artifact(bucket, "data-health/latest.json", payload)
    safe_publish_json_artifact(
        bucket,
        f"data-health/history/{manifest.manifest_date.isoformat()}.json",
        payload,
    )


def _task_counts_by_type(manifest: CollectionManifest) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for task in manifest.tasks:
        type_counts = counts.setdefault(
            task.task_type.value,
            {
                "total": 0,
                "pending": 0,
                "leased": 0,
                "running": 0,
                "succeeded": 0,
                "failed": 0,
                "retry_wait": 0,
                "retry_exhausted": 0,
            },
        )
        type_counts["total"] += 1
        type_counts[task.status.value] = type_counts.get(task.status.value, 0) + 1
    return counts


def _task_rows(
    manifest: CollectionManifest,
    failed_only: bool,
) -> list[dict[str, Any]]:
    rows = []
    for task in sorted(
        manifest.tasks,
        key=lambda item: item.updated_at or item.created_at,
        reverse=True,
    ):
        if failed_only and task.status != CollectionTaskStatus.FAILED:
            continue
        rows.append(
            {
                "task_id": task.task_id,
                "task_type": task.task_type.value,
                "status": task.status.value,
                "ticker_count": len(task.tickers),
                "ticker_range_start": task.ticker_range_start,
                "ticker_range_end": task.ticker_range_end,
                "start_date": task.start_date,
                "end_date": task.end_date,
                "reason": task.reason,
                "failure_reason": task.failure_reason,
                "attempts": task.attempts,
                "max_attempts": task.max_attempts,
                "updated_at": task.updated_at,
                "output_counts": (
                    task.output_counts.model_dump(mode="json")
                    if task.output_counts
                    else {}
                ),
            }
        )
    return rows


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "body": body}
