"""Dividend calendar collector for Phase 1 signals."""

import os
import re
import time
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
from src.models.schemas import (
    CollectionOutputCounts,
    CollectionTaskType,
    RepairMode,
    RepairModeRequest,
)
from src.services.secrets import get_provider_api_key
from src.services.collection_manifest import (
    complete_task,
    find_task,
    load_manifest,
    mark_task_running,
    write_manifest,
)
from src.services.calendar_artifacts import (
    publish_calendar_artifacts,
    publish_calendar_provider_snapshots,
)

logger = structlog.get_logger(__name__)
DATE_TYPE = date
DATETIME_TYPE = datetime
FINNHUB_DIVIDEND_URL = "https://finnhub.io/api/v1/stock/dividend"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

CLOUDWATCH_NAMESPACE = "StockMonitoring"
DEFAULT_LOOKBACK_DAYS = int(os.environ.get("DIVIDEND_CALENDAR_LOOKBACK_DAYS", "1825"))
DEFAULT_LOOKAHEAD_DAYS = int(os.environ.get("DIVIDEND_CALENDAR_LOOKAHEAD_DAYS", "120"))
DEFAULT_HISTORY_LIMIT = int(os.environ.get("DIVIDEND_CALENDAR_HISTORY_LIMIT", "80"))
DEFAULT_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS = float(
    os.environ.get("DIVIDEND_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS", "0")
)
DEFAULT_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION = int(
    os.environ.get("DIVIDEND_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION", "25")
)
MAX_TICKERS_PER_RUN = 50
ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
COLLECTION_MANIFEST_BUCKET = os.environ.get(
    "COLLECTION_MANIFEST_BUCKET",
    ARTIFACT_BUCKET,
)
_LAST_ALPHA_VANTAGE_REQUEST_AT = 0.0
_ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED = False
_ALPHA_VANTAGE_DIVIDEND_CALL_COUNT = 0
_ALPHA_VANTAGE_DIVIDEND_CALL_BUDGET = DEFAULT_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION


@dataclass
class ManifestTaskRun:
    bucket: str
    key: str
    task_id: str


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Collect dividend calendar/history events for active stocks."""
    event = event or {}
    event = _calendar_repair_event(event)
    log = logger.bind(lambda_event=event)
    log.info("dividend_collector_started")
    manifest_task_run: ManifestTaskRun | None = None
    DatabasePool.initialize()
    try:
        _reset_alpha_vantage_invocation_state(event)
        manifest_task_run = _prepare_manifest_task_run(event, context)
        stocks = _select_stocks(store.active_stock_metadata(), event)
        collection_date = date.today()
        range_start = collection_date - timedelta(
            days=int(event.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        )
        range_end = collection_date + timedelta(
            days=int(event.get("lookahead_days", DEFAULT_LOOKAHEAD_DAYS))
        )
        repair_mode = str(event.get("repair_mode") or "")
        if event.get("dry_run"):
            return {
                "statusCode": 200,
                "body": {
                    "status": "dry_run",
                    "mode": repair_mode or event.get("mode", "dividend_calendar"),
                    "events_collected": 0,
                    "selected_ticker_count": len(stocks),
                    "selected_tickers": [stock["ticker"] for stock in stocks],
                    "provider_budget": event.get("provider_budget", {}),
                    "range_start": range_start.isoformat(),
                    "range_end": range_end.isoformat(),
                },
            }
        stored_count = 0
        failed_tickers: list[str] = []
        collected_events: list[dict[str, Any]] = []
        provider_events: list[dict[str, Any]] = []
        zero_event_tickers: list[str] = []

        for stock in stocks:
            ticker = stock["ticker"]
            try:
                events = fetch_dividend_events(
                    ticker,
                    company_name=stock.get("company_name"),
                    history_limit=int(event.get("history_limit", DEFAULT_HISTORY_LIMIT)),
                    start_date=range_start,
                    end_date=range_end,
                    provider_events=provider_events,
                )
                for dividend_event in events:
                    enriched = enrich_price_reaction(dividend_event)
                    store.put_dividend_event(enriched)
                    collected_events.append(enriched)
                    stored_count += 1
                if not events:
                    zero_event_tickers.append(ticker)
            except Exception as exc:
                failed_tickers.append(ticker)
                log.warning("dividend_ticker_collection_failed", ticker=ticker, error=str(exc))

        provider_health = _build_provider_health(
            selected_ticker_count=len(stocks),
            stored_count=stored_count,
            provider_event_count=len(provider_events),
            failed_tickers=failed_tickers,
        )
        warnings = _calendar_warnings(provider_health)
        response_status = _response_status(failed_tickers, provider_health)
        artifact_scope = manifest_task_run.task_id if manifest_task_run else None
        publish_latest_artifacts = manifest_task_run is None
        publish_calendar_artifacts(
            bucket=str(event.get("artifact_bucket") or ARTIFACT_BUCKET),
            event_type="dividends",
            events=collected_events,
            collection_date=collection_date,
            range_start=range_start,
            range_end=range_end,
            selected_tickers=[stock["ticker"] for stock in stocks],
            collection_status=response_status,
            provider_health=provider_health,
            warnings=warnings,
            zero_event_tickers=zero_event_tickers,
            artifact_scope=artifact_scope,
            publish_latest=publish_latest_artifacts,
        )
        publish_calendar_provider_snapshots(
            bucket=str(event.get("artifact_bucket") or ARTIFACT_BUCKET),
            event_type="dividends",
            provider_events=provider_events,
            collection_date=collection_date,
            range_start=range_start,
            range_end=range_end,
            selected_tickers=[stock["ticker"] for stock in stocks],
            artifact_scope=artifact_scope,
            publish_latest=publish_latest_artifacts,
        )

        _emit_metric("dividend_events_collected", stored_count)
        _emit_metric("dividend_collection_failed_tickers", len(failed_tickers))
        _emit_metric("dividend_zero_event_tickers", len(zero_event_tickers))
        _emit_metric(
            "dividend_provider_degraded_runs",
            1 if provider_health["status"] == "degraded" else 0,
        )
        log.info(
            "dividend_collector_completed",
            selected_ticker_count=len(stocks),
            events_collected=stored_count,
            failed_ticker_count=len(failed_tickers),
            provider_health=provider_health,
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
                "status": response_status,
                **({"mode": repair_mode} if repair_mode else {}),
                "events_collected": stored_count,
                "selected_ticker_count": len(stocks),
                "failed_tickers": failed_tickers,
                "zero_event_tickers": zero_event_tickers,
                "provider_health": provider_health,
                "warnings": warnings,
            },
        }
    except Exception as exc:
        if manifest_task_run:
            _fail_manifest_task_run(manifest_task_run, str(exc))
        raise
    finally:
        DatabasePool.close()


def _calendar_repair_event(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("mode") != RepairMode.REPAIR_CALENDARS:
        return event
    request = RepairModeRequest.model_validate(event)
    provider_budget = dict(request.provider_budget)
    repair_event = dict(event)
    repair_event["mode"] = "repair_calendars"
    repair_event["repair_mode"] = RepairMode.REPAIR_CALENDARS.value
    repair_event["provider_budget"] = provider_budget
    repair_event["dry_run"] = request.dry_run
    if request.tickers:
        repair_event["tickers"] = request.tickers
        repair_event["max_tickers"] = min(
            request.max_tickers or len(request.tickers),
            len(request.tickers),
        )
    elif request.max_tickers is not None:
        repair_event["max_tickers"] = request.max_tickers
    if "alpha_vantage" in provider_budget:
        repair_event["alpha_vantage_max_calls"] = provider_budget["alpha_vantage"]
    return repair_event


def _select_stocks(stocks: list[dict[str, Any]], event: dict[str, Any]) -> list[dict[str, Any]]:
    requested = {str(ticker).upper() for ticker in event.get("tickers", [])}
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]
    ticker_offset = max(int(event.get("ticker_offset", 0)), 0)
    max_tickers = int(event.get("max_tickers", MAX_TICKERS_PER_RUN))
    sorted_stocks = sorted(stocks, key=lambda stock: stock["ticker"])
    return sorted_stocks[ticker_offset : ticker_offset + max_tickers]


def _build_provider_health(
    *,
    selected_ticker_count: int,
    stored_count: int,
    provider_event_count: int,
    failed_tickers: list[str],
) -> dict[str, Any]:
    if selected_ticker_count <= 0:
        return {"status": "ok", "provider": "yfinance"}
    if stored_count == 0 and provider_event_count == 0 and not failed_tickers:
        return {
            "status": "degraded",
            "provider": "yfinance",
            "reason": "provider_returned_zero_events",
            "message": (
                "No dividend events or raw provider rows were collected for the "
                "selected tickers. Recent runs show yfinance 429 throttling, so "
                "this should be treated as a provider/data-quality gap."
            ),
        }
    return {"status": "ok", "provider": "yfinance"}


def _calendar_warnings(provider_health: dict[str, Any]) -> list[str]:
    if provider_health.get("status") == "degraded":
        return [str(provider_health.get("message") or provider_health.get("reason"))]
    return []


def _response_status(
    failed_tickers: list[str],
    provider_health: dict[str, Any],
) -> str:
    if provider_health.get("status") == "degraded":
        return "degraded"
    if failed_tickers:
        return "partial"
    return "success"


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
    provider_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch historical and available upcoming dividend events."""
    yf_ticker = yf.Ticker(ticker)
    today = date.today()
    info = _safe_info(yf_ticker)
    dividend_yield = _to_decimal(info.get("dividendYield"))
    if dividend_yield is not None and dividend_yield < 1:
        dividend_yield *= Decimal("100")

    events: list[dict[str, Any]] = []
    try:
        dividends = getattr(yf_ticker, "dividends", None)
    except Exception as exc:
        logger.warning(
            "yfinance_dividend_history_unavailable",
            ticker=ticker.upper(),
            error=str(exc),
        )
        dividends = None
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
            if provider_events is not None:
                provider_events.append(
                    _raw_provider_event(
                        ticker=ticker.upper(),
                        company_name=company_name,
                        ex_dividend_date=ex_date,
                        source_url=f"https://finance.yahoo.com/quote/{ticker.upper()}/history?filter=div",
                        raw_fields={
                            "ex_dividend_date": ex_date,
                            "dividend_amount": _serialize_raw_value(amount),
                            "dividend_yield": _serialize_raw_value(dividend_yield),
                            "source": "dividends_series",
                        },
                    )
                )
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
        if provider_events is not None:
            provider_events.append(
                _raw_provider_event(
                    ticker=ticker.upper(),
                    company_name=company_name,
                    ex_dividend_date=upcoming["ex_dividend_date"],
                    source_url=f"https://finance.yahoo.com/quote/{ticker.upper()}/history?filter=div",
                    raw_fields={
                        "exDividendDate": _serialize_raw_value(info.get("exDividendDate")),
                        "dividendRate": _serialize_raw_value(info.get("dividendRate")),
                        "dividendYield": _serialize_raw_value(info.get("dividendYield")),
                        "source": "ticker_info",
                    },
                )
        )
        events.append(upcoming)
    if events:
        return events

    events = fetch_finnhub_dividend_events(
        ticker,
        company_name=company_name,
        start_date=start_date,
        end_date=end_date,
        provider_events=provider_events,
    )
    if events:
        return events

    return fetch_alpha_vantage_dividend_events(
        ticker,
        company_name=company_name,
        start_date=start_date,
        end_date=end_date,
        provider_events=provider_events,
    )


def fetch_finnhub_dividend_events(
    ticker: str,
    company_name: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    provider_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch dividend events from Finnhub when yfinance has no usable rows."""
    api_key = _finnhub_api_key()
    if not api_key:
        logger.warning("finnhub_api_key_not_configured_for_dividend_calendar")
        return []

    today = date.today()
    range_start = start_date or today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    range_end = end_date or today + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)
    try:
        response = requests.get(
            FINNHUB_DIVIDEND_URL,
            params={
                "symbol": ticker.upper(),
                "from": range_start.isoformat(),
                "to": range_end.isoformat(),
                "token": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(
            "finnhub_dividend_events_unavailable",
            ticker=ticker.upper(),
            error=_safe_provider_error(exc),
        )
        return []
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("dividends", [])

    events: list[dict[str, Any]] = []
    seen_dates: set[date] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        ex_date = _normalize_date(row.get("exDate") or row.get("date"))
        if ex_date is None or ex_date in seen_dates:
            continue
        if ex_date < range_start or ex_date > range_end:
            continue
        seen_dates.add(ex_date)
        amount = _to_decimal(row.get("amount") or row.get("adjustedAmount"))
        pay_date = _normalize_date(row.get("payDate") or row.get("paymentDate"))
        if provider_events is not None:
            provider_events.append(
                _raw_provider_event(
                    provider="finnhub",
                    ticker=ticker.upper(),
                    company_name=company_name,
                    ex_dividend_date=ex_date,
                    source_url="https://finnhub.io/docs/api/stock-dividends",
                    raw_fields={
                        str(key): _serialize_raw_value(value)
                        for key, value in row.items()
                    },
                )
            )
        events.append(
            _event(
                ticker,
                company_name,
                ex_date,
                amount=amount,
                dividend_yield=None,
                is_upcoming=ex_date >= today,
                pay_date=pay_date,
                provider="finnhub",
                source_url="https://finnhub.io/docs/api/stock-dividends",
            )
        )
    logger.info("finnhub_dividend_events_fetched", ticker=ticker.upper(), count=len(events))
    return events


def fetch_alpha_vantage_dividend_events(
    ticker: str,
    company_name: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    provider_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch dividend events from Alpha Vantage corporate actions."""
    api_key = _alpha_vantage_api_key()
    if not api_key:
        logger.warning("alpha_vantage_api_key_not_configured_for_dividend_calendar")
        return []
    if _ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED:
        logger.warning(
            "alpha_vantage_dividend_quota_skipped",
            ticker=ticker.upper(),
            reason="quota_exhausted",
        )
        return []
    if _ALPHA_VANTAGE_DIVIDEND_CALL_COUNT >= _ALPHA_VANTAGE_DIVIDEND_CALL_BUDGET:
        logger.warning(
            "alpha_vantage_dividend_budget_skipped",
            ticker=ticker.upper(),
            call_budget=_ALPHA_VANTAGE_DIVIDEND_CALL_BUDGET,
        )
        return []

    today = date.today()
    range_start = start_date or today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    range_end = end_date or today + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)
    try:
        _pace_alpha_vantage_request()
        _record_alpha_vantage_call()
        response = requests.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "DIVIDENDS",
                "symbol": ticker.upper(),
                "apikey": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(
            "alpha_vantage_dividend_events_unavailable",
            ticker=ticker.upper(),
            error=_safe_provider_error(exc),
        )
        return []

    payload = response.json()
    provider_error = _alpha_vantage_payload_error(payload)
    if provider_error:
        if _is_alpha_vantage_quota_error(provider_error):
            _mark_alpha_vantage_quota_exhausted()
        logger.warning(
            "alpha_vantage_dividend_payload_unavailable",
            ticker=ticker.upper(),
            error=_safe_provider_error(provider_error),
        )
        return []

    events: list[dict[str, Any]] = []
    seen_dates: set[date] = set()
    for row in payload.get("data", []):
        if not isinstance(row, dict):
            continue
        ex_date = _normalize_date(row.get("ex_dividend_date") or row.get("exDate"))
        if ex_date is None or ex_date in seen_dates:
            continue
        if ex_date < range_start or ex_date > range_end:
            continue
        seen_dates.add(ex_date)
        amount = _to_decimal(row.get("amount") or row.get("dividend_amount"))
        pay_date = _normalize_date(row.get("payment_date") or row.get("payDate"))
        if provider_events is not None:
            provider_events.append(
                _raw_provider_event(
                    provider="alpha_vantage",
                    ticker=ticker.upper(),
                    company_name=company_name,
                    ex_dividend_date=ex_date,
                    source_url="https://www.alphavantage.co/documentation/#dividends",
                    raw_fields={
                        str(key): _serialize_raw_value(value)
                        for key, value in row.items()
                    },
                )
            )
        events.append(
            _event(
                ticker,
                company_name,
                ex_date,
                amount=amount,
                dividend_yield=None,
                is_upcoming=ex_date >= today,
                pay_date=pay_date,
                provider="alpha_vantage",
                source_url="https://www.alphavantage.co/documentation/#dividends",
            )
        )
    logger.info(
        "alpha_vantage_dividend_events_fetched",
        ticker=ticker.upper(),
        count=len(events),
    )
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
    provider: str = "yfinance",
    source_url: str | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "company_name": company_name,
        "ex_dividend_date": ex_dividend_date,
        "pay_date": pay_date.isoformat() if pay_date else None,
        "dividend_amount": amount,
        "dividend_yield": dividend_yield,
        "is_upcoming": is_upcoming,
        "provider": provider,
        "source_url": source_url
        or f"https://finance.yahoo.com/quote/{ticker.upper()}/history?filter=div",
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


def _raw_provider_event(
    *,
    provider: str = "yfinance",
    ticker: str,
    company_name: str | None,
    ex_dividend_date: date,
    source_url: str,
    raw_fields: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": provider,
        "ticker": ticker,
        "company_name": company_name,
        "ex_dividend_date": ex_dividend_date.isoformat(),
        "source_url": source_url,
        "raw_fields": raw_fields,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def _finnhub_api_key() -> str | None:
    return get_provider_api_key(
        "finnhub",
        "FINNHUB_KEY",
        "FINNHUB_KEY_SECRET_NAME",
        supported_json_keys=("FINNHUB_KEY", "finnhub_key", "api_key"),
    )


def _alpha_vantage_api_key() -> str | None:
    return get_provider_api_key(
        "alpha_vantage",
        "ALPHA_VANTAGE_API_KEY",
        "ALPHA_VANTAGE_API_KEY_SECRET_NAME",
        supported_json_keys=("ALPHA_VANTAGE_API_KEY", "alpha_vantage_api_key", "api_key"),
    )


def _pace_alpha_vantage_request() -> None:
    global _LAST_ALPHA_VANTAGE_REQUEST_AT
    interval = max(DEFAULT_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS, 0)
    if interval <= 0:
        return
    now = time.monotonic()
    elapsed = now - _LAST_ALPHA_VANTAGE_REQUEST_AT
    if _LAST_ALPHA_VANTAGE_REQUEST_AT and elapsed < interval:
        wait_seconds = interval - elapsed
        logger.info("alpha_vantage_dividend_request_paced", wait_seconds=wait_seconds)
        time.sleep(wait_seconds)
    _LAST_ALPHA_VANTAGE_REQUEST_AT = time.monotonic()


def _reset_alpha_vantage_invocation_state(event: dict[str, Any]) -> None:
    global _ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED
    global _ALPHA_VANTAGE_DIVIDEND_CALL_COUNT
    global _ALPHA_VANTAGE_DIVIDEND_CALL_BUDGET
    _ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED = False
    _ALPHA_VANTAGE_DIVIDEND_CALL_COUNT = 0
    _ALPHA_VANTAGE_DIVIDEND_CALL_BUDGET = max(
        int(
            event.get(
                "alpha_vantage_max_calls",
                DEFAULT_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION,
            )
        ),
        0,
    )


def _record_alpha_vantage_call() -> None:
    global _ALPHA_VANTAGE_DIVIDEND_CALL_COUNT
    _ALPHA_VANTAGE_DIVIDEND_CALL_COUNT += 1


def _safe_provider_error(error: object) -> str:
    message = str(error)
    message = re.sub(r"([?&](?:token|apikey)=)[^&\s]+", r"\1***", message)
    return re.sub(
        r"((?:api\s+)?key\s+(?:as|is)\s+)[A-Za-z0-9_-]+",
        r"\1***",
        message,
        flags=re.IGNORECASE,
    )


def _is_alpha_vantage_quota_error(error: object) -> bool:
    message = str(error).lower()
    return (
        "rate limit" in message
        or "standard api call frequency" in message
        or "25 requests per day" in message
    )


def _mark_alpha_vantage_quota_exhausted() -> None:
    global _ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED
    _ALPHA_VANTAGE_DIVIDEND_QUOTA_EXHAUSTED = True


def _alpha_vantage_payload_error(payload: dict[str, Any]) -> str | None:
    for key in ("Error Message", "Note", "Information"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _serialize_raw_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, DATETIME_TYPE):
        return value.isoformat()
    if isinstance(value, DATE_TYPE):
        return value.isoformat()
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            return str(value)
    return value


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
