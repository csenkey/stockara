"""Earnings calendar collector for Phase 1 signals."""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
import pandas as pd
import requests
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
from src.services.secrets import get_provider_api_key

logger = structlog.get_logger(__name__)

CLOUDWATCH_NAMESPACE = "StockMonitoring"
DEFAULT_LOOKBACK_DAYS = 730
DEFAULT_LOOKAHEAD_DAYS = int(os.environ.get("EARNINGS_CALENDAR_LOOKAHEAD_DAYS", "45"))
DEFAULT_LIMIT = 12
MAX_TICKERS_PER_RUN = 50
FINNHUB_EARNINGS_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"
COLLECTION_MANIFEST_BUCKET = os.environ.get(
    "COLLECTION_MANIFEST_BUCKET",
    os.environ.get("STOCKARA_ARTIFACT_BUCKET", ""),
)


@dataclass
class ManifestTaskRun:
    bucket: str
    key: str
    task_id: str


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Collect upcoming and recent past earnings events for active stocks."""
    event = event or {}
    log = logger.bind(lambda_event=event)
    log.info("earnings_collector_started")
    manifest_task_run: ManifestTaskRun | None = None
    DatabasePool.initialize()
    try:
        manifest_task_run = _prepare_manifest_task_run(event, context)
        stocks = store.active_stock_metadata()
        selected = (
            _select_stocks(stocks, event)
            if manifest_task_run
            else sorted(stocks, key=lambda stock: stock["ticker"])
        )
        stored_count = 0
        failed_tickers: list[str] = []

        if manifest_task_run:
            stored_count, failed_tickers = _collect_per_ticker(selected, event, log)
        else:
            events = fetch_earnings_calendar_events(
                stocks,
                lookahead_days=int(
                    event.get("lookahead_days", DEFAULT_LOOKAHEAD_DAYS)
                ),
            )
            for earnings_event in events:
                enriched = enrich_price_reaction(earnings_event)
                store.put_earnings_event(enriched)
                stored_count += 1

        _emit_metric("earnings_events_collected", stored_count)
        _emit_metric("earnings_collection_failed_tickers", len(failed_tickers))
        log.info(
            "earnings_collector_completed",
            selected_ticker_count=len(selected),
            events_collected=stored_count,
            failed_ticker_count=len(failed_tickers),
        )
        if manifest_task_run:
            _complete_manifest_task_run(
                manifest_task_run,
                selected_ticker_count=len(selected),
                stored_count=stored_count,
                failed_tickers=failed_tickers,
            )
        return {
            "statusCode": 200,
            "body": {
                "status": "partial" if failed_tickers else "success",
                "events_collected": stored_count,
                "selected_ticker_count": len(selected),
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


def _collect_per_ticker(
    selected: list[dict[str, Any]],
    event: dict[str, Any],
    log: Any,
) -> tuple[int, list[str]]:
    stored_count = 0
    failed_tickers: list[str] = []
    events_by_key: dict[tuple[str, date], dict[str, Any]] = {}
    try:
        for earnings_event in fetch_earnings_calendar_events(
            selected,
            lookahead_days=int(event.get("lookahead_days", DEFAULT_LOOKAHEAD_DAYS)),
        ):
            events_by_key[
                (earnings_event["ticker"], earnings_event["event_date"])
            ] = earnings_event
    except Exception as exc:
        log.warning("earnings_calendar_range_collection_failed", error=str(exc))

    for stock in selected:
        ticker = stock["ticker"]
        try:
            events = fetch_earnings_events(
                ticker,
                company_name=stock.get("company_name"),
                limit=int(event.get("limit", DEFAULT_LIMIT)),
            )
            for earnings_event in events:
                events_by_key[
                    (earnings_event["ticker"], earnings_event["event_date"])
                ] = earnings_event
        except Exception as exc:
            failed_tickers.append(ticker)
            log.warning("earnings_ticker_collection_failed", ticker=ticker, error=str(exc))
    for earnings_event in events_by_key.values():
        enriched = enrich_price_reaction(earnings_event)
        store.put_earnings_event(enriched)
        stored_count += 1
    return stored_count, failed_tickers


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
    if task.task_type != CollectionTaskType.EARNINGS:
        raise ValueError(f"Task {task_id} is not an earnings collection task")
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
            "earnings_manifest_task_completion_failed",
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
            "earnings_manifest_task_failure_write_failed",
            task_id=task_run.task_id,
            error=str(exc),
        )


def fetch_earnings_events(
    ticker: str,
    company_name: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch and normalize earnings calendar rows from yfinance."""
    yf_ticker = yf.Ticker(ticker)
    if hasattr(yf_ticker, "get_earnings_dates"):
        data = yf_ticker.get_earnings_dates(limit=limit)
    else:
        data = getattr(yf_ticker, "earnings_dates", None)
    if data is None or getattr(data, "empty", True):
        return []

    today = date.today()
    events: list[dict[str, Any]] = []
    for raw_date, row in data.iterrows():
        event_date = _normalize_event_date(raw_date)
        if event_date is None:
            continue
        events.append(
            {
                "ticker": ticker.upper(),
                "company_name": company_name,
                "event_date": event_date,
                "eps_estimate": _to_decimal(_row_value(row, "EPS Estimate")),
                "reported_eps": _to_decimal(_row_value(row, "Reported EPS")),
                "surprise_percent": _to_decimal(_row_value(row, "Surprise(%)")),
                "time_of_day": _normalize_time_of_day(_row_value(row, "Earnings Date")),
                "is_upcoming": event_date >= today,
                "provider": "yfinance",
                "source_url": f"https://finance.yahoo.com/quote/{ticker.upper()}/analysis",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return events


def fetch_earnings_calendar_events(
    stocks: list[dict[str, Any]],
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> list[dict[str, Any]]:
    """Fetch upcoming earnings in one date-range request, filtered to watchlist."""
    api_key = _finnhub_api_key()
    if not api_key:
        logger.warning("finnhub_api_key_not_configured_for_earnings_calendar")
        return []

    today = date.today()
    end_date = today + timedelta(days=max(lookahead_days, 1))
    response = requests.get(
        FINNHUB_EARNINGS_CALENDAR_URL,
        params={
            "from": today.isoformat(),
            "to": end_date.isoformat(),
            "token": api_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    active_by_ticker = {
        str(stock.get("ticker", "")).upper(): stock
        for stock in stocks
        if stock.get("ticker")
    }
    events: list[dict[str, Any]] = []
    for row in payload.get("earningsCalendar", []):
        ticker = str(row.get("symbol", "")).upper()
        if ticker not in active_by_ticker:
            continue
        event_date = _normalize_event_date(row.get("date"))
        if event_date is None:
            continue
        stock = active_by_ticker[ticker]
        events.append(
            {
                "ticker": ticker,
                "company_name": stock.get("company_name"),
                "event_date": event_date,
                "eps_estimate": _to_decimal(row.get("epsEstimate")),
                "reported_eps": _to_decimal(row.get("epsActual")),
                "surprise_percent": _to_decimal(row.get("surprisePercent")),
                "time_of_day": _normalize_time_of_day(row.get("hour")),
                "is_upcoming": event_date >= today,
                "provider": "finnhub",
                "source_url": "https://finnhub.io/calendar/earnings",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    logger.info("earnings_calendar_events_fetched", count=len(events))
    return events


def _finnhub_api_key() -> str | None:
    return get_provider_api_key(
        "finnhub",
        "FINNHUB_KEY",
        "FINNHUB_KEY_SECRET_NAME",
        supported_json_keys=("FINNHUB_KEY", "finnhub_key", "api_key"),
    )


def enrich_price_reaction(event: dict[str, Any]) -> dict[str, Any]:
    """Attach pre/post earnings price movement for past events when OHLCV exists."""
    if event.get("is_upcoming"):
        return event
    event_date = event["event_date"]
    rows = store.get_stock_data(
        event["ticker"],
        event_date - timedelta(days=7),
        event_date + timedelta(days=7),
    )
    before = _nearest_row_before(rows, event_date)
    after = _nearest_row_after(rows, event_date)
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
        "post_earnings_price_move_percent": move.quantize(Decimal("0.01")),
    }


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


def _normalize_event_date(value: Any) -> date | None:
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


def _normalize_time_of_day(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).lower()
    if "before" in text or "bmo" in text:
        return "before_market"
    if "after" in text or "amc" in text:
        return "after_market"
    return None


def _row_value(row: Any, column: str) -> Any:
    try:
        return row.get(column)
    except AttributeError:
        return None


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
        logger.warning("earnings_metric_emit_failed", metric=metric_name, error=str(exc))
