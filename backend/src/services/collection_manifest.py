"""Helpers for reading and updating collection manifests in S3."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

import boto3

from src.db.connection import store
from src.models.schemas import (
    CollectionCoverageGate,
    CollectionManifest,
    CollectionOutputCounts,
    CollectionTask,
    CollectionTaskStatus,
    CollectionTaskType,
)
from src.services.provider_health import classify_collection_health

CLOUDWATCH_NAMESPACE = "StockMonitoring"

RATE_LIMIT_RETRY_HOURS = {
    "alpha_vantage": 24,
    "newsapi": 24,
    "finnhub": 1,
}

TRANSIENT_RETRY_BASE_MINUTES = {
    "price": 30,
    "news": 15,
    "earnings": 60,
    "dividend": 60,
    "yfinance": 30,
    "nasdaq": 30,
    "stooq": 30,
    "alpha_vantage": 60,
    "newsapi": 15,
    "finnhub": 15,
}
MAX_TRANSIENT_RETRY_MINUTES = 6 * 60
TASK_MUTATION_MAX_ATTEMPTS = 8


def load_manifest(bucket: str, key: str) -> CollectionManifest:
    response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
    payload = json.loads(response["Body"].read().decode("utf-8"))
    return CollectionManifest.model_validate(payload)


def write_manifest(bucket: str, key: str, manifest: CollectionManifest) -> None:
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=manifest.model_dump_json(indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    emit_manifest_metrics(manifest)


def manifest_date_from_key(key: str) -> date:
    filename = key.rsplit("/", 1)[-1]
    return date.fromisoformat(filename.removesuffix(".json"))


def refresh_manifest_task_state(
    manifest: CollectionManifest,
    *,
    seed_if_missing: bool = True,
) -> CollectionManifest:
    """Overlay the S3 snapshot with authoritative per-task DynamoDB state."""
    rows = store.collection_manifest_tasks(manifest.manifest_date)
    if not rows and seed_if_missing:
        store.seed_collection_manifest_tasks(
            manifest.manifest_date,
            [task.model_dump(mode="json") for task in manifest.tasks],
        )
        rows = store.collection_manifest_tasks(manifest.manifest_date)
    if rows:
        manifest.tasks = [
            CollectionTask.model_validate(row)
            for row in sorted(rows, key=lambda row: str(row["task_id"]))
        ]
        recompute_summary(manifest)
    return manifest


def get_persisted_manifest_task(
    manifest_date: date,
    task_id: str,
) -> CollectionTask:
    row = store.get_collection_manifest_task(manifest_date, task_id)
    if row is None:
        raise ValueError(f"Collection manifest task not found: {task_id}")
    return CollectionTask.model_validate(row)


def lease_persisted_manifest_task(
    manifest_date: date,
    task_id: str,
    *,
    lease_owner: str,
    lease_expires_at: datetime,
    now: datetime,
) -> CollectionTask | None:
    def mutate(task: CollectionTask) -> bool:
        ready = task.status == CollectionTaskStatus.PENDING
        if task.status == CollectionTaskStatus.RETRY_WAIT:
            ready = task.next_retry_at is None or task.next_retry_at <= now
        if task.status in {
            CollectionTaskStatus.LEASED,
            CollectionTaskStatus.RUNNING,
        }:
            ready = bool(task.lease_expires_at and task.lease_expires_at <= now)
        if not ready:
            return False
        task.status = CollectionTaskStatus.LEASED
        task.lease_owner = lease_owner
        task.lease_expires_at = lease_expires_at
        task.updated_at = now
        return True

    task, changed = _mutate_persisted_manifest_task(
        manifest_date,
        task_id,
        mutate,
    )
    return task if changed else None


def mark_persisted_manifest_task_running(
    manifest_date: date,
    task_id: str,
    *,
    lease_owner: str | None,
    now: datetime | None = None,
) -> CollectionTask:
    now = now or datetime.now(timezone.utc)

    def mutate(task: CollectionTask) -> bool:
        if task.status in {
            CollectionTaskStatus.SUCCEEDED,
            CollectionTaskStatus.FAILED,
            CollectionTaskStatus.SKIPPED,
        }:
            return False
        if (
            task.status == CollectionTaskStatus.RUNNING
            and task.lease_owner == lease_owner
        ):
            return False
        mark_task_running(task_manifest(task), task_id, lease_owner, now)
        return True

    task, _ = _mutate_persisted_manifest_task(manifest_date, task_id, mutate)
    return task


def complete_persisted_manifest_task(
    manifest_date: date,
    task_id: str,
    output_counts: CollectionOutputCounts,
    *,
    failed: bool = False,
    failure_reason: str | None = None,
    now: datetime | None = None,
) -> CollectionTask:
    now = now or datetime.now(timezone.utc)

    def mutate(task: CollectionTask) -> bool:
        if task.status in {
            CollectionTaskStatus.SUCCEEDED,
            CollectionTaskStatus.FAILED,
            CollectionTaskStatus.SKIPPED,
        }:
            return False
        complete_task(
            task_manifest(task),
            task_id,
            output_counts,
            failed=failed,
            failure_reason=failure_reason,
            now=now,
        )
        return True

    task, _ = _mutate_persisted_manifest_task(manifest_date, task_id, mutate)
    return task


def _mutate_persisted_manifest_task(
    manifest_date: date,
    task_id: str,
    mutate: Callable[[CollectionTask], bool],
) -> tuple[CollectionTask, bool]:
    for _ in range(TASK_MUTATION_MAX_ATTEMPTS):
        row = store.get_collection_manifest_task(manifest_date, task_id)
        if row is None:
            raise ValueError(f"Collection manifest task not found: {task_id}")
        version = int(row.get("version", 0))
        task = CollectionTask.model_validate(row)
        if not mutate(task):
            return task, False
        if store.replace_collection_manifest_task(
            manifest_date,
            task.model_dump(mode="json"),
            version,
        ):
            return task, True
    raise RuntimeError(
        f"Collection manifest task update conflicted repeatedly: {task_id}"
    )


def task_manifest(task: CollectionTask) -> CollectionManifest:
    """Build the smallest valid manifest needed by shared lifecycle helpers."""
    return CollectionManifest(
        manifest_date=task.created_at.date(),
        generated_at=task.created_at,
        updated_at=task.updated_at,
        active_ticker_count=len(task.tickers),
        task_types=[task.task_type],
        tasks=[task],
    )


def emit_manifest_metrics(manifest: CollectionManifest) -> None:
    """Emit daily manifest health metrics for alarms and dashboards."""
    now = datetime.now(timezone.utc)
    summary = manifest.summary
    failed_gate_count = sum(1 for gate in summary.coverage_gates if not gate.passed)
    retry_exhausted_tasks = sum(
        1
        for task in manifest.tasks
        if task.status == CollectionTaskStatus.FAILED
        and task.attempts >= task.max_attempts
    )
    provider_failure_tasks = sum(
        1
        for task in manifest.tasks
        if task.status in {CollectionTaskStatus.FAILED, CollectionTaskStatus.RETRY_WAIT}
    )
    incomplete_tasks = (
        summary.pending_tasks
        + summary.leased_tasks
        + summary.running_tasks
        + summary.retry_wait_tasks
        + summary.failed_tasks
    )
    metric_data = [
        {
            "MetricName": "collection_manifest_age_minutes",
            "Value": max((now - manifest.updated_at).total_seconds() / 60, 0),
            "Unit": "Minutes",
        },
        {
            "MetricName": "collection_manifest_incomplete_tasks",
            "Value": incomplete_tasks,
            "Unit": "Count",
        },
        {
            "MetricName": "collection_manifest_failed_tasks",
            "Value": summary.failed_tasks,
            "Unit": "Count",
        },
        {
            "MetricName": "collection_manifest_retry_wait_tasks",
            "Value": summary.retry_wait_tasks,
            "Unit": "Count",
        },
        {
            "MetricName": "collection_manifest_retry_exhausted_tasks",
            "Value": retry_exhausted_tasks,
            "Unit": "Count",
        },
        {
            "MetricName": "collection_manifest_low_coverage_gates",
            "Value": failed_gate_count,
            "Unit": "Count",
        },
        {
            "MetricName": "collection_manifest_coverage_percent",
            "Value": float(summary.coverage_ratio) * 100,
            "Unit": "Percent",
        },
        {
            "MetricName": "collection_provider_failure_tasks",
            "Value": provider_failure_tasks,
            "Unit": "Count",
        },
    ]
    try:
        boto3.client("cloudwatch").put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=metric_data,
        )
    except Exception:
        # Collection writes must not fail only because observability is unavailable.
        return


def find_task(manifest: CollectionManifest, task_id: str) -> CollectionTask:
    for task in manifest.tasks:
        if task.task_id == task_id:
            return task
    raise ValueError(f"Collection manifest task not found: {task_id}")


def mark_task_running(
    manifest: CollectionManifest,
    task_id: str,
    lease_owner: str | None = None,
    now: datetime | None = None,
) -> CollectionTask:
    now = now or datetime.now(timezone.utc)
    task = find_task(manifest, task_id)
    task.status = CollectionTaskStatus.RUNNING
    task.started_at = task.started_at or now
    task.updated_at = now
    task.attempts += 1
    task.lease_owner = lease_owner
    recompute_summary(manifest)
    return task


def complete_task(
    manifest: CollectionManifest,
    task_id: str,
    output_counts: CollectionOutputCounts,
    failed: bool = False,
    failure_reason: str | None = None,
    now: datetime | None = None,
) -> CollectionTask:
    now = now or datetime.now(timezone.utc)
    task = find_task(manifest, task_id)
    task.status = _completion_status(task, failed, failure_reason)
    task.completed_at = now
    task.updated_at = now
    task.failure_reason = failure_reason
    task.output_counts = output_counts
    task.lease_owner = None
    task.lease_expires_at = None
    task.next_retry_at = _next_retry_at(task, failure_reason, now) if failed else None
    recompute_summary(manifest)
    return task


def retry_delay_for_failure(task: CollectionTask, reason: str | None) -> timedelta | None:
    health = classify_collection_health(reason)
    if health.value in {
        "healthy",
        "provider_unsupported",
        "symbol_mapping_needed",
        "inactive_or_delisted",
    }:
        return None

    provider = (task.provider or task.task_type.value).lower()
    if health.value == "rate_limited":
        return timedelta(hours=RATE_LIMIT_RETRY_HOURS.get(provider, 1))

    attempt_index = max(task.attempts - 1, 0)
    base_minutes = TRANSIENT_RETRY_BASE_MINUTES.get(provider, 30)
    delay_minutes = min(
        base_minutes * (2**attempt_index),
        MAX_TRANSIENT_RETRY_MINUTES,
    )
    return timedelta(minutes=delay_minutes)


def _completion_status(
    task: CollectionTask,
    failed: bool,
    failure_reason: str | None,
) -> CollectionTaskStatus:
    if not failed:
        return CollectionTaskStatus.SUCCEEDED
    if task.attempts >= task.max_attempts:
        return CollectionTaskStatus.FAILED
    if retry_delay_for_failure(task, failure_reason) is None:
        return CollectionTaskStatus.FAILED
    return CollectionTaskStatus.RETRY_WAIT


def _next_retry_at(
    task: CollectionTask,
    failure_reason: str | None,
    now: datetime,
) -> datetime | None:
    if task.status != CollectionTaskStatus.RETRY_WAIT:
        return None
    delay = retry_delay_for_failure(task, failure_reason)
    return now + delay if delay is not None else None


def recompute_summary(manifest: CollectionManifest) -> None:
    status_counts: dict[CollectionTaskStatus, int] = {
        status: 0 for status in CollectionTaskStatus
    }
    successful_tickers = 0
    failed_tickers = 0
    for task in manifest.tasks:
        status_counts[task.status] += 1
        successful_tickers += task.output_counts.successful_tickers
        failed_tickers += task.output_counts.failed_tickers

    total_tickers = manifest.summary.total_tickers or manifest.active_ticker_count
    covered_tickers = min(successful_tickers, total_tickers)
    coverage_ratio = Decimal(covered_tickers) / Decimal(total_tickers or 1)
    manifest.summary.total_tasks = len(manifest.tasks)
    manifest.summary.pending_tasks = status_counts[CollectionTaskStatus.PENDING]
    manifest.summary.leased_tasks = status_counts[CollectionTaskStatus.LEASED]
    manifest.summary.running_tasks = status_counts[CollectionTaskStatus.RUNNING]
    manifest.summary.succeeded_tasks = status_counts[CollectionTaskStatus.SUCCEEDED]
    manifest.summary.failed_tasks = status_counts[CollectionTaskStatus.FAILED]
    manifest.summary.retry_wait_tasks = status_counts[CollectionTaskStatus.RETRY_WAIT]
    manifest.summary.skipped_tasks = status_counts[CollectionTaskStatus.SKIPPED]
    manifest.summary.total_tickers = total_tickers
    manifest.summary.successful_tickers = successful_tickers
    manifest.summary.failed_tickers = failed_tickers
    manifest.summary.coverage_ratio = coverage_ratio.quantize(Decimal("0.0001"))
    _recompute_coverage_gates(manifest)
    manifest.updated_at = datetime.now(timezone.utc)


def _recompute_coverage_gates(manifest: CollectionManifest) -> None:
    existing_by_name = {gate.name: gate for gate in manifest.summary.coverage_gates}
    manifest.summary.coverage_gates = [
        _coverage_gate(
            existing_by_name,
            name="price_freshness",
            observed_value=_task_type_ticker_ratio(
                manifest,
                {CollectionTaskType.PRICE},
                publication_gate_only=True,
            ),
            default_required=Decimal("0.9"),
            unit="ratio",
            message=(
                "Share of active tickers with completed fresh price collection. "
                "Tickers with stale or missing prices are excluded individually."
            ),
        ),
        _coverage_gate(
            existing_by_name,
            name="news_freshness",
            observed_value=_task_type_completion_ratio(manifest, {CollectionTaskType.NEWS}),
            default_required=Decimal("1"),
            unit="ratio",
            message=(
                "Share of manifest news chunks completed. Manual news collection can "
                "refresh news/latest.json without completing every manifest chunk."
            ),
        ),
        _coverage_gate(
            existing_by_name,
            name="calendar_coverage",
            observed_value=_task_type_ticker_ratio(
                manifest,
                {CollectionTaskType.EARNINGS, CollectionTaskType.DIVIDEND},
            ),
            default_required=Decimal("0.9"),
            unit="ratio",
            message=(
                "Share of active tickers with calendar scan attempts. Missing upcoming "
                "earnings or dividend events are context gaps, not candidate blockers."
            ),
        ),
    ]


def _coverage_gate(
    existing_by_name: dict[str, CollectionCoverageGate],
    name: str,
    observed_value: Decimal,
    default_required: Decimal,
    unit: str,
    message: str,
) -> CollectionCoverageGate:
    existing = existing_by_name.get(name)
    required = existing.required_value if existing else default_required
    observed = observed_value.quantize(Decimal("0.0001"))
    return CollectionCoverageGate(
        name=name,
        passed=observed >= required,
        observed_value=observed,
        required_value=required,
        unit=unit,
        message=message,
    )


def _task_type_ticker_ratio(
    manifest: CollectionManifest,
    task_types: set[CollectionTaskType],
    publication_gate_only: bool = False,
) -> Decimal:
    relevant = [
        task
        for task in manifest.tasks
        if task.task_type in task_types
        and (not publication_gate_only or _counts_toward_publication_gate(task))
    ]
    total = sum(len(task.tickers) for task in relevant)
    if total == 0:
        return Decimal("1")
    successful = sum(task.output_counts.successful_tickers for task in relevant)
    return Decimal(min(successful, total)) / Decimal(total)


def _counts_toward_publication_gate(task: CollectionTask) -> bool:
    """Exclude ad hoc gap/backfill tasks from daily publication freshness gates."""
    return not (task.start_date or task.end_date or str(task.reason or "").strip())


def _task_type_completion_ratio(
    manifest: CollectionManifest,
    task_types: set[CollectionTaskType],
) -> Decimal:
    relevant = [task for task in manifest.tasks if task.task_type in task_types]
    if not relevant:
        return Decimal("1")
    succeeded = sum(
        1 for task in relevant if task.status == CollectionTaskStatus.SUCCEEDED
    )
    return Decimal(succeeded) / Decimal(len(relevant))


def stock_output_counts_from_summary(summary: dict[str, Any]) -> CollectionOutputCounts:
    return CollectionOutputCounts(
        records_fetched=int(summary.get("records_collected", 0) or 0),
        records_written=int(summary.get("records_collected", 0) or 0),
        duplicate_records=int(summary.get("duplicate_record_count", 0) or 0),
        malformed_records=int(summary.get("malformed_ticker_count", 0) or 0),
        failed_records=int(summary.get("failed_ticker_count", 0) or 0),
        successful_tickers=int(summary.get("successful_ticker_count", 0) or 0),
        failed_tickers=int(summary.get("failed_ticker_count", 0) or 0),
    )
