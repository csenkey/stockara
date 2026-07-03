"""Dividend calendar collector for Phase 1 signals."""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
import pandas as pd
import structlog
import yfinance as yf

from src.db.connection import DatabasePool, store
from src.models.schemas import CollectionOutputCounts, CollectionTaskType
from src.services.collection_manifest import (
    complete_task,
    find_task,
    load_manifest,
    mark_task_running,
    write_manifest,
)
from src.services.calendar_artifacts import publish_calendar_artifacts

logger = structlog.get_logger(__name__)

CLOUDWATCH_NAMESPACE = "StockMonitoring"
DEFAULT_LOOKBACK_DAYS = int(os.environ.get("DIVIDEND_CALENDAR_LOOKBACK_DAYS", "1825"))
DEFAULT_LOOKAHEAD_DAYS = int(os.environ.get("DIVIDEND_CALENDAR_LOOKAHEAD_DAYS", "120"))
DEFAULT_HISTORY_LIMIT = int(os.environ.get("DIVIDEND_CALENDAR_HISTORY_LIMIT", "80"))
MAX_TICKERS_PER_RUN = 50
ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
COLLECTION_MANIFEST_BUCKET = os.environ.get(
    "COLLECTION_MANIFEST_BUCKET",
    ARTIFACT_BUCKET,
)


@dataclass
class ManifestTaskRun:
    bucket: str
    key: str
    task_id: str


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Collect dividend calendar/history events for active stocks."""
    event = event or {}
    log = logger.bind(lambda_event=event)
    log.info("dividend_collector_started")
    manifest_task_run: ManifestTaskRun | None = None
    DatabasePool.initialize()
    try:
        manifest_task_run = _prepare_manifest_task_run(event, context)
        stocks = _select_stocks(store.active_stock_metadata(), event)
        collection_date = date.today()
        range_start = collection_date - timedelta(
            days=int(event.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        )
        range_end = collection_date + timedelta(
            days=int(event.get("lookahead_days", DEFAULT_LOOKAHEAD_DAYS))
        )
        stored_count = 0
        failed_tickers: list[str] = []
        collected_events: list[dict[str, Any]] = []

        for stock in stocks:
            ticker = stock["ticker"]
            try:
                events = fetch_dividend_events(
                    ticker,
                    company_name=stock.get("company_name"),
                    history_limit=int(event.get("history_limit", DEFAULT_HISTORY_LIMIT)),
                    start_date=range_start,
                    end_date=range_end,
                )
                for dividend_event in events:
                    enriched = enrich_price_reaction(dividend_event)
                    store.put_dividend_event(enriched)
                    collected_events.append(enriched)
                    stored_count += 1
            except Exception as exc:
                failed_tickers.append(ticker)
                log.warning("dividend_ticker_collection_failed", ticker=ticker, error=str(exc))

        publish_calendar_artifacts(
            bucket=str(event.get("artifact_bucket") or ARTIFACT_BUCKET),
            event_type="dividends",
            events=collected_events,
            collection_date=collection_date,
            range_start=range_start,
            range_end=range_end,
            selected_tickers=[stock["ticker"] for stock in stocks],
        )

        _emit_metric("dividend_events_collected", stored_count)
        _emit_metric("dividend_collection_failed_tickers", len(failed_tickers))
        log.info(
            "dividend_collector_completed",
            selected_ticker_count=len(stocks),
            events_collected=stored_count,
            failed_ticker_count=len(failed_tickers),
        )
        if manifest_task_run:
            _complete_manifest_task_run(
                manifest_task_run,
                selected_ticker_count=len(stocks),
                stored_count=stored_count,
                failed_tickers=failed_tickers,
            )
        return {
            "statusCode": 200,
            "body": {
                "status": "partial" if failed_tickers else "success",
                "events_collected": stored_count,
                "selected_ticker_count": len(stocks),
                "failed_tickers": failed_tickers,
            },
        }
    except Exception as exc:
        if manifest_task_run:
            _fail_manifest_task_run(manifest_task_run, str(exc))
        raise
    finally:
        DatabasePool.close()


def _select_stocks(stocks: list[dict[str, Any]], event: dict[str, Any]) -> list[dict[str, Any]]:
    requested = {str(ticker).upper() for ticker in event.get("tickers", [])}
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]
    max_tickers = int(event.get("max_tickers", MAX_TICKERS_PER_RUN))
    return sorted(stocks, key=lambda stock: stock["ticker"])[:max_tickers]


def _prepare_manifest_task_run(
    event: dict[str, Any],
    context: Any,
) -> ManifestTaskRun | None:
    if event.get("mode") != "manifest_task":
        return None
    bucket = str(event.get("manifest_bucket") or COLLECTION_MANIFEST_BUCKET).strip()
    key = str(event.get("manifest_key") or "").strip()
    task_id = str(event.get("task_id") or "").strip()
    if not bucket or not key or not task_id:
        raise ValueError("manifest_bucket, manifest_key, and task_id are required")

    manifest = load_manifest(bucket, key)
    task = find_task(manifest, task_id)
    if task.task_type != CollectionTaskType.DIVIDEND:
        raise ValueError(f"Task {task_id} is not a dividend collection task")
    event["tickers"] = task.tickers
    event["max_tickers"] = len(task.tickers)
    lease_owner = getattr(context, "aws_request_id", None) if context else None
    mark_task_running(manifest, task_id, lease_owner=lease_owner)
    write_manifest(bucket, key, manifest)
    return ManifestTaskRun(bucket=bucket, key=key, task_id=task_id)


def _complete_manifest_task_run(
    task_run: ManifestTaskRun,
    selected_ticker_count: int,
    stored_count: int,
    failed_tickers: list[str],
) -> None:
    try:
        failed_count = len(set(failed_tickers))
        output_counts = CollectionOutputCounts(
            records_fetched=stored_count,
            records_written=stored_count,
            failed_records=failed_count,
            successful_tickers=max(selected_ticker_count - failed_count, 0),
            failed_tickers=failed_count,
        )
        manifest = load_manifest(task_run.bucket, task_run.key)
        complete_task(
            manifest,
            task_run.task_id,
            output_counts,
            failed=failed_count > 0,
            failure_reason="partial_ticker_failure" if failed_count else None,
        )
        write_manifest(task_run.bucket, task_run.key, manifest)
    except Exception as exc:
        logger.warning(
            "dividend_manifest_task_completion_failed",
            task_id=task_run.task_id,
            error=str(exc),
        )


def _fail_manifest_task_run(task_run: ManifestTaskRun, reason: str) -> None:
    try:
        manifest = load_manifest(task_run.bucket, task_run.key)
        complete_task(
            manifest,
            task_run.task_id,
            CollectionOutputCounts(),
            failed=True,
            failure_reason=reason,
        )
        write_manifest(task_run.bucket, task_run.key, manifest)
    except Exception as exc:
        logger.warning(
            "dividend_manifest_task_failure_write_failed",
            task_id=task_run.task_id,
            error=str(exc),
        )


def fetch_dividend_events(
    ticker: str,
    company_name: str | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Fetch historical and available upcoming dividend events from yfinance."""
    yf_ticker = yf.Ticker(ticker)
    today = date.today()
    info = _safe_info(yf_ticker)
    dividend_yield = _to_decimal(info.get("dividendYield"))
    if dividend_yield is not None and dividend_yield < 1:
        dividend_yield *= Decimal("100")

    events: list[dict[str, Any]] = []
    dividends = getattr(yf_ticker, "dividends", None)
    if dividends is not None and len(dividends) > 0:
        recent = dividends
        if start_date is not None:
            recent = recent[
                [
                    (_normalize_date(raw_date) or date.min) >= start_date
                    for raw_date in recent.index
                ]
            ]
        recent = recent.tail(history_limit)
        for raw_date, amount in recent.items():
            ex_date = _normalize_date(raw_date)
            if ex_date is None:
                continue
            if end_date is not None and ex_date > end_date:
                continue
            events.append(
                _event(
                    ticker,
                    company_name,
                    ex_date,
                    amount=_to_decimal(amount),
                    dividend_yield=dividend_yield,
                    is_upcoming=ex_date >= today,
                )
            )

    upcoming = _upcoming_from_info(ticker, company_name, info, dividend_yield, today)
    if upcoming and end_date is not None and upcoming["ex_dividend_date"] > end_date:
        upcoming = None
    if upcoming and not any(
        event["ex_dividend_date"] == upcoming["ex_dividend_date"] for event in events
    ):
        events.append(upcoming)
    return events


def enrich_price_reaction(event: dict[str, Any]) -> dict[str, Any]:
    """Attach ex-dividend price movement for past events when OHLCV exists."""
    if event.get("is_upcoming"):
        return event
    ex_date = event["ex_dividend_date"]
    rows = store.get_stock_data(
        event["ticker"],
        ex_date - timedelta(days=7),
        ex_date + timedelta(days=7),
    )
    before = _nearest_row_before(rows, ex_date)
    after = _nearest_row_after(rows, ex_date)
    if not before or not after:
        return event
    price_before = _analysis_close_price(before)
    price_after = _analysis_close_price(after)
    if price_before <= 0:
        return event
    move = (price_after - price_before) / price_before * Decimal("100")
    return {
        **event,
        "price_before": price_before,
        "price_after": price_after,
        "post_ex_dividend_price_move_percent": move.quantize(Decimal("0.01")),
    }


def _event(
    ticker: str,
    company_name: str | None,
    ex_dividend_date: date,
    amount: Decimal | None,
    dividend_yield: Decimal | None,
    is_upcoming: bool,
    pay_date: date | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "company_name": company_name,
        "ex_dividend_date": ex_dividend_date,
        "pay_date": pay_date,
        "dividend_amount": amount,
        "dividend_yield": dividend_yield,
        "is_upcoming": is_upcoming,
        "provider": "yfinance",
        "source_url": f"https://finance.yahoo.com/quote/{ticker.upper()}/history?filter=div",
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def _upcoming_from_info(
    ticker: str,
    company_name: str | None,
    info: dict[str, Any],
    dividend_yield: Decimal | None,
    today: date,
) -> dict[str, Any] | None:
    ex_date = _timestamp_to_date(info.get("exDividendDate"))
    if ex_date is None or ex_date < today:
        return None
    return _event(
        ticker,
        company_name,
        ex_date,
        amount=_to_decimal(info.get("dividendRate")),
        dividend_yield=dividend_yield,
        is_upcoming=True,
    )


def _safe_info(yf_ticker: Any) -> dict[str, Any]:
    try:
        return getattr(yf_ticker, "info", {}) or {}
    except Exception:
        return {}


def _nearest_row_before(rows: list[dict[str, Any]], event_date: date) -> dict[str, Any] | None:
    candidates = [
        row for row in rows if (_parse_date(row.get("trading_date")) or date.max) < event_date
    ]
    return max(candidates, key=lambda row: _parse_date(row.get("trading_date")) or date.min, default=None)


def _nearest_row_after(rows: list[dict[str, Any]], event_date: date) -> dict[str, Any] | None:
    candidates = [
        row for row in rows if (_parse_date(row.get("trading_date")) or date.min) > event_date
    ]
    return min(candidates, key=lambda row: _parse_date(row.get("trading_date")) or date.max, default=None)


def _analysis_close_price(row: dict[str, Any]) -> Decimal:
    adjusted = row.get("adjusted_close_price")
    if adjusted is not None:
        return Decimal(str(adjusted))
    return Decimal(str(row["close_price"]))


def _normalize_date(value: Any) -> date | None:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _timestamp_to_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (ValueError, TypeError, OSError):
        return _normalize_date(value)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    try:
        return Decimal(str(round(float(value), 4)))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _emit_metric(metric_name: str, value: float) -> None:
    try:
        boto3.client("cloudwatch").put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count",
                    "Timestamp": datetime.now(timezone.utc),
                }
            ],
        )
    except Exception as exc:
        logger.warning("dividend_metric_emit_failed", metric=metric_name, error=str(exc))
