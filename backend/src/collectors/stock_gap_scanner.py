"""Detect missing stock price rows and enqueue targeted backfill tasks."""

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import boto3
import structlog

from src.db.connection import DatabasePool, store
from src.models.schemas import (
    CollectionManifest,
    CollectionTask,
    CollectionTaskType,
    collection_manifest_s3_key,
)
from src.services.collection_manifest import recompute_summary, write_manifest
from src.services.static_artifacts import safe_publish_json_artifact

logger = structlog.get_logger(__name__)

ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
SCAN_LOOKBACK_DAYS = int(os.environ.get("STOCK_GAP_SCAN_LOOKBACK_DAYS", "90"))
MAX_TASKS_PER_RUN = int(os.environ.get("STOCK_GAP_SCAN_MAX_TASKS", "250"))
MAX_RANGE_DAYS = int(os.environ.get("STOCK_GAP_TASK_MAX_RANGE_DAYS", "14"))
MARKET_CLOSE_BUFFER_HOURS = int(
    os.environ.get("STOCK_GAP_MARKET_CLOSE_BUFFER_HOURS", "2")
)


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Scan active tickers for missing recent price rows and append backfill tasks."""
    event = event or {}
    bucket = str(event.get("bucket") or ARTIFACT_BUCKET).strip()
    if not bucket:
        return _response(
            500,
            {
                "status": "failed",
                "message": "STOCKARA_ARTIFACT_BUCKET is not configured",
            },
        )

    now = _event_now(event)
    manifest_date = _manifest_date(event, now)
    key = collection_manifest_s3_key(manifest_date)
    scan_end = _scan_end_date(event, now)
    scan_start = _scan_start_date(event, scan_end)
    requested_tickers = {
        str(ticker).strip().upper()
        for ticker in event.get("tickers", [])
        if str(ticker).strip()
    }

    DatabasePool.initialize()
    try:
        stocks = store.active_stock_metadata()
        if requested_tickers:
            stocks = [
                stock
                for stock in stocks
                if str(stock.get("ticker", "")).upper() in requested_tickers
            ]
        manifest = _load_manifest(bucket, key)
        if manifest is None:
            return _response(
                404,
                {
                    "status": "failed",
                    "message": f"Collection manifest not found: {key}",
                },
            )
        existing_task_ids = {task.task_id for task in manifest.tasks}
        tasks = _gap_tasks_for_stocks(
            stocks,
            scan_start,
            scan_end,
            now,
            existing_task_ids,
            MAX_TASKS_PER_RUN,
        )
        manifest.tasks.extend(tasks)
        recompute_summary(manifest)
        if tasks:
            write_manifest(bucket, key, manifest)
        _publish_price_gaps_artifact(
            bucket,
            manifest_key=key,
            manifest_date=manifest_date,
            scan_start=scan_start,
            scan_end=scan_end,
            active_ticker_count=len(stocks),
            tasks=tasks,
            generated_at=now,
        )
    finally:
        DatabasePool.close()

    _emit_metric("stock_price_gaps_detected", len(tasks))
    logger.info(
        "stock_gap_scan_completed",
        manifest_key=key,
        scan_start=scan_start.isoformat(),
        scan_end=scan_end.isoformat(),
        active_ticker_count=len(stocks),
        tasks_created=len(tasks),
    )
    return _response(
        200,
        {
            "status": "success",
            "bucket": bucket,
            "manifest_key": key,
            "scan_start_date": scan_start.isoformat(),
            "scan_end_date": scan_end.isoformat(),
            "active_ticker_count": len(stocks),
            "tasks_created": len(tasks),
            "task_ids": [task.task_id for task in tasks],
        },
    )


def _gap_tasks_for_stocks(
    stocks: list[dict[str, Any]],
    scan_start: date,
    scan_end: date,
    now: datetime,
    existing_task_ids: set[str],
    max_tasks: int,
) -> list[CollectionTask]:
    tasks: list[CollectionTask] = []
    for stock in sorted(stocks, key=lambda item: str(item.get("ticker", ""))):
        if len(tasks) >= max_tasks:
            break
        ticker = str(stock.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        rows = store.get_stock_data(ticker, scan_start, scan_end)
        stored_dates = {
            date.fromisoformat(str(row["trading_date"])[:10])
            for row in rows
            if row.get("trading_date")
        }
        missing_dates = [
            trading_date
            for trading_date in _trading_days(scan_start, scan_end)
            if trading_date not in stored_dates
        ]
        for start, end in _date_ranges(missing_dates):
            for chunk_start, chunk_end in _split_range(start, end, MAX_RANGE_DAYS):
                task_id = _task_id(ticker, chunk_start, chunk_end)
                if task_id in existing_task_ids:
                    continue
                tasks.append(
                    CollectionTask(
                        task_id=task_id,
                        task_type=CollectionTaskType.PRICE,
                        tickers=[ticker],
                        ticker_range_start=ticker,
                        ticker_range_end=ticker,
                        start_date=chunk_start,
                        end_date=chunk_end,
                        reason="missing_stock_data_gap",
                        created_at=now,
                        updated_at=now,
                    )
                )
                existing_task_ids.add(task_id)
                if len(tasks) >= max_tasks:
                    break
            if len(tasks) >= max_tasks:
                break
    return tasks


def _scan_end_date(event: dict[str, Any], now: datetime) -> date:
    if event.get("scan_end_date"):
        return date.fromisoformat(str(event["scan_end_date"]))
    market_close_utc = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if now < market_close_utc + timedelta(hours=MARKET_CLOSE_BUFFER_HOURS):
        return now.date() - timedelta(days=1)
    return now.date()


def _scan_start_date(event: dict[str, Any], scan_end: date) -> date:
    if event.get("scan_start_date"):
        return date.fromisoformat(str(event["scan_start_date"]))
    lookback_days = int(event.get("lookback_days", SCAN_LOOKBACK_DAYS))
    return scan_end - timedelta(days=lookback_days)


def _event_now(event: dict[str, Any]) -> datetime:
    if event.get("now"):
        value = datetime.fromisoformat(str(event["now"]).replace("Z", "+00:00"))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _manifest_date(event: dict[str, Any], now: datetime) -> date:
    if event.get("manifest_date"):
        return date.fromisoformat(str(event["manifest_date"]))
    return now.date()


def _load_manifest(bucket: str, key: str) -> CollectionManifest | None:
    try:
        response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        payload = json.loads(response["Body"].read().decode("utf-8"))
        return CollectionManifest.model_validate(payload)
    except Exception as exc:
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if error_code not in {"NoSuchKey", "404", "NotFound"}:
            logger.warning("stock_gap_manifest_load_failed", key=key, error=str(exc))
        return None


def _trading_days(start: date, end: date) -> list[date]:
    if end < start:
        return []
    days: list[date] = []
    current = start
    holidays = _market_holidays(start.year, end.year)
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            days.append(current)
        current += timedelta(days=1)
    return days


def _date_ranges(days: list[date]) -> list[tuple[date, date]]:
    if not days:
        return []
    ranges: list[tuple[date, date]] = []
    start = previous = days[0]
    for current in days[1:]:
        if _next_trading_day(previous) == current:
            previous = current
            continue
        ranges.append((start, previous))
        start = previous = current
    ranges.append((start, previous))
    return ranges


def _split_range(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=max_days - 1), end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks


def _next_trading_day(day: date) -> date:
    current = day + timedelta(days=1)
    holidays = _market_holidays(day.year, current.year + 1)
    while current.weekday() >= 5 or current in holidays:
        current += timedelta(days=1)
    return current


def _market_holidays(start_year: int, end_year: int) -> set[date]:
    holidays: set[date] = set()
    for year in range(start_year, end_year + 1):
        holidays.update(
            {
                _observed(date(year, 1, 1)),
                _nth_weekday(year, 1, 0, 3),
                _nth_weekday(year, 2, 0, 3),
                _good_friday(year),
                _last_weekday(year, 5, 0),
                _observed(date(year, 6, 19)),
                _observed(date(year, 7, 4)),
                _nth_weekday(year, 9, 0, 1),
                _nth_weekday(year, 11, 3, 4),
                _observed(date(year, 12, 25)),
            }
        )
    return holidays


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year + int(month == 12), 1 if month == 12 else month + 1, 1)
    current -= timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _good_friday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * offset) // 451
    month = (h + offset - 7 * m + 114) // 31
    day = ((h + offset - 7 * m + 114) % 31) + 1
    return date(year, month, day) - timedelta(days=2)


def _task_id(ticker: str, start: date, end: date) -> str:
    return f"price-backfill-{ticker}-{start.isoformat()}-{end.isoformat()}"


def _emit_metric(name: str, value: float) -> None:
    try:
        boto3.client("cloudwatch").put_metric_data(
            Namespace="StockMonitoring",
            MetricData=[{"MetricName": name, "Value": value, "Unit": "Count"}],
        )
    except Exception:
        return


def _publish_price_gaps_artifact(
    bucket: str,
    manifest_key: str,
    manifest_date: date,
    scan_start: date,
    scan_end: date,
    active_ticker_count: int,
    tasks: list[CollectionTask],
    generated_at: datetime,
) -> None:
    gaps_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        ticker = task.tickers[0] if task.tickers else task.ticker_range_start
        if not ticker:
            continue
        gaps_by_ticker.setdefault(str(ticker), []).append(
            {
                "start_date": task.start_date,
                "end_date": task.end_date,
                "task_id": task.task_id,
                "status": task.status.value,
                "reason": task.reason,
                "trading_day_count": len(
                    _trading_days(task.start_date, task.end_date)
                    if task.start_date and task.end_date
                    else []
                ),
            }
        )

    ticker_rows = [
        {
            "ticker": ticker,
            "gap_count": len(gaps),
            "missing_trading_days": sum(gap["trading_day_count"] for gap in gaps),
            "gaps": gaps,
        }
        for ticker, gaps in gaps_by_ticker.items()
    ]
    ticker_rows.sort(key=lambda item: item["missing_trading_days"], reverse=True)
    payload = {
        "generated_at": generated_at.isoformat(),
        "manifest_date": manifest_date.isoformat(),
        "manifest_key": manifest_key,
        "scan_start_date": scan_start.isoformat(),
        "scan_end_date": scan_end.isoformat(),
        "active_ticker_count": active_ticker_count,
        "gap_ticker_count": len(ticker_rows),
        "gap_count": len(tasks),
        "missing_trading_days": sum(
            row["missing_trading_days"] for row in ticker_rows
        ),
        "by_ticker": ticker_rows,
    }
    safe_publish_json_artifact(bucket, "price-gaps/latest.json", payload)
    safe_publish_json_artifact(
        bucket,
        f"price-gaps/history/{manifest_date.isoformat()}.json",
        payload,
    )


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "body": body}
