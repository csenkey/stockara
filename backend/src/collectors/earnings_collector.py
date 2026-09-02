"""Earnings calendar collector for Phase 1 signals."""

import csv
import io
import json
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
from src.services.collection_manifest import (
    complete_persisted_manifest_task,
    get_persisted_manifest_task,
    manifest_date_from_key,
    mark_persisted_manifest_task_running,
)
from src.services.calendar_artifacts import (
    publish_calendar_artifacts,
    publish_calendar_provider_snapshots,
)
from src.services.earnings_history_coverage import (
    INCOMPLETE_COLLECTION_OUTCOMES,
    build_earnings_history_coverage,
    publish_earnings_history_coverage,
)
from src.services.secrets import get_provider_api_key

logger = structlog.get_logger(__name__)
DATE_TYPE = date
DATETIME_TYPE = datetime

CLOUDWATCH_NAMESPACE = "StockMonitoring"
DEFAULT_LOOKBACK_DAYS = int(os.environ.get("EARNINGS_CALENDAR_LOOKBACK_DAYS", "1825"))
DEFAULT_LOOKAHEAD_DAYS = int(os.environ.get("EARNINGS_CALENDAR_LOOKAHEAD_DAYS", "120"))
DEFAULT_LIMIT = int(os.environ.get("EARNINGS_CALENDAR_YFINANCE_LIMIT", "32"))
DEFAULT_FALLBACK_MAX_TICKERS = int(
    os.environ.get("EARNINGS_CALENDAR_FALLBACK_MAX_TICKERS", "25")
)
DEFAULT_EARNINGS_CONFLICT_WINDOW_DAYS = int(
    os.environ.get("EARNINGS_CONFLICT_WINDOW_DAYS", "14")
)
DEFAULT_CONFLICT_CONFIRMATION_HORIZON_DAYS = int(
    os.environ.get("EARNINGS_CONFLICT_CONFIRMATION_HORIZON_DAYS", "7")
)
DEFAULT_CONFLICT_CONFIRMATION_MAX_TICKERS = int(
    os.environ.get("EARNINGS_CONFLICT_CONFIRMATION_MAX_TICKERS", "10")
)
MAX_TICKERS_PER_RUN = 50
FINNHUB_EARNINGS_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
ALPHA_VANTAGE_EARNINGS_CALENDAR_SOURCE_URL = (
    "https://www.alphavantage.co/documentation/#earnings-calendar"
)
DEFAULT_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS = float(
    os.environ.get("EARNINGS_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS", "0")
)
DEFAULT_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION = int(
    os.environ.get("EARNINGS_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION", "20")
)
ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
COLLECTION_MANIFEST_BUCKET = os.environ.get(
    "COLLECTION_MANIFEST_BUCKET",
    ARTIFACT_BUCKET,
)
_LAST_ALPHA_VANTAGE_REQUEST_AT = 0.0
_ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED = False
_ALPHA_VANTAGE_EARNINGS_CALL_COUNT = 0
_ALPHA_VANTAGE_EARNINGS_CALL_BUDGET = DEFAULT_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION


@dataclass
class ManifestTaskRun:
    bucket: str
    key: str
    manifest_date: date
    task_id: str


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Collect upcoming and recent past earnings events for active stocks."""
    event = event or {}
    event = _calendar_repair_event(event)
    log = logger.bind(lambda_event=event)
    log.info("earnings_collector_started")
    manifest_task_run: ManifestTaskRun | None = None
    DatabasePool.initialize()
    try:
        _reset_alpha_vantage_invocation_state(event)
        manifest_task_run = _prepare_manifest_task_run(event, context)
        stocks = store.active_stock_metadata()
        selected = (
            _select_stocks(stocks, event)
            if manifest_task_run or _has_explicit_ticker_selection(event)
            else sorted(stocks, key=lambda stock: stock["ticker"])
        )
        full_watchlist_selection = _is_full_watchlist_selection(stocks, selected)
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
                    "mode": repair_mode or event.get("mode", "earnings_calendar"),
                    "events_collected": 0,
                    "selected_ticker_count": len(selected),
                    "selected_tickers": [stock["ticker"] for stock in selected],
                    "provider_budget": event.get("provider_budget", {}),
                    "range_start": range_start.isoformat(),
                    "range_end": range_end.isoformat(),
                },
            }
        collected_events: list[dict[str, Any]] = []
        provider_events: list[dict[str, Any]] = []
        failed_tickers: list[str] = []
        provider_attempts: dict[str, dict[str, Any]] = {}
        ticker_collection_outcomes: dict[str, str] = {}

        if manifest_task_run:
            collected_events, failed_tickers = _collect_per_ticker(
                selected,
                event,
                log,
                range_start=range_start,
                range_end=range_end,
                provider_events=provider_events,
                provider_attempts=provider_attempts,
                ticker_collection_outcomes=ticker_collection_outcomes,
            )
        else:
            events = fetch_earnings_calendar_events(
                selected,
                start_date=max(range_start, collection_date),
                end_date=range_end,
                provider_events=provider_events,
                provider_attempts=provider_attempts,
            )
            if not events:
                fallback_stocks = _select_rotating_fallback_stocks(
                    selected,
                    event,
                    collection_date,
                )
                fallback_events, fallback_failures = _collect_per_ticker(
                    fallback_stocks,
                    event,
                    log,
                    range_start=range_start,
                    range_end=range_end,
                    provider_events=provider_events,
                    provider_attempts=provider_attempts,
                    ticker_collection_outcomes=ticker_collection_outcomes,
                    include_range_calendar=False,
                )
                events = fallback_events
                failed_tickers.extend(fallback_failures)
            for earnings_event in events:
                collected_events.append(enrich_price_reaction(earnings_event))

        stored_count = _store_events(collected_events)
        history_end = collection_date - timedelta(days=1)
        stored_history = _load_stored_history(
            selected=selected,
            range_start=range_start,
            range_end=history_end,
            full_watchlist=full_watchlist_selection,
        )
        history_coverage = build_earnings_history_coverage(
            tickers=[stock["ticker"] for stock in selected],
            events=stored_history,
            as_of=collection_date,
            collection_outcomes=ticker_collection_outcomes,
        )
        event_tickers = {str(item.get("ticker") or "").upper() for item in collected_events}
        zero_event_tickers = [
            stock["ticker"] for stock in selected if stock["ticker"] not in event_tickers
        ]
        provider_health = _build_provider_health(
            selected_ticker_count=len(selected),
            stored_count=stored_count,
            failed_tickers=failed_tickers,
            provider_attempts=provider_attempts,
        )
        warnings = _calendar_warnings(provider_health)
        reconciliation_counts = _reconciliation_counts(collected_events)
        conflict_confirmation_attempts = int(
            provider_attempts.get("yfinance_conflict_confirmation", {}).get(
                "attempt_count", 0
            )
        )
        response_status = _response_status(failed_tickers, provider_health)
        artifact_scope = (
            manifest_task_run.task_id
            if manifest_task_run
            else str(event.get("artifact_scope") or "").strip() or (
                "targeted" if not full_watchlist_selection else None
            )
        )
        publish_latest_artifacts = (
            manifest_task_run is None
            and full_watchlist_selection
        )
        publish_calendar_artifacts(
            bucket=str(event.get("artifact_bucket") or ARTIFACT_BUCKET),
            event_type="earnings",
            events=collected_events,
            collection_date=collection_date,
            range_start=range_start,
            range_end=range_end,
            selected_tickers=[stock["ticker"] for stock in selected],
            collection_status=response_status,
            provider_health=provider_health,
            warnings=warnings,
            zero_event_tickers=zero_event_tickers,
            artifact_scope=artifact_scope,
            publish_latest=publish_latest_artifacts,
        )
        publish_calendar_provider_snapshots(
            bucket=str(event.get("artifact_bucket") or ARTIFACT_BUCKET),
            event_type="earnings",
            provider_events=provider_events,
            collection_date=collection_date,
            range_start=range_start,
            range_end=range_end,
            selected_tickers=[stock["ticker"] for stock in selected],
            artifact_scope=artifact_scope,
            publish_latest=publish_latest_artifacts,
        )
        publish_earnings_history_coverage(
            bucket=str(event.get("artifact_bucket") or ARTIFACT_BUCKET),
            payload=history_coverage,
            artifact_scope=artifact_scope,
            publish_latest=publish_latest_artifacts,
        )

        _emit_metric("earnings_events_collected", stored_count)
        _emit_metric("earnings_collection_failed_tickers", len(failed_tickers))
        _emit_metric("earnings_zero_event_tickers", len(zero_event_tickers))
        _emit_metric(
            "earnings_history_complete_tickers",
            history_coverage["summary"]["complete_ticker_count"],
        )
        _emit_metric(
            "earnings_history_incomplete_tickers",
            history_coverage["summary"]["incomplete_ticker_count"],
        )
        _emit_metric(
            "earnings_history_collection_skips",
            history_coverage["summary"]["incomplete_collection_ticker_count"],
        )
        _emit_metric(
            "earnings_confirmed_events",
            reconciliation_counts["confirmed"],
        )
        _emit_metric(
            "earnings_single_source_events",
            reconciliation_counts["single_source"],
        )
        _emit_metric(
            "earnings_conflicting_candidates",
            reconciliation_counts["conflicting"],
        )
        _emit_metric(
            "earnings_conflict_confirmation_attempts",
            conflict_confirmation_attempts,
        )
        _emit_metric(
            "earnings_provider_degraded_runs",
            1 if provider_health["status"] == "degraded" else 0,
        )
        log.info(
            "earnings_collector_completed",
            selected_ticker_count=len(selected),
            events_collected=stored_count,
            failed_ticker_count=len(failed_tickers),
            provider_health=provider_health,
        )
        if manifest_task_run:
            _complete_manifest_task_run(
                manifest_task_run,
                selected_ticker_count=len(selected),
                stored_count=stored_count,
                failed_tickers=failed_tickers,
                provider_health=provider_health,
                ticker_collection_outcomes=ticker_collection_outcomes,
            )
        return {
            "statusCode": 200,
            "body": {
                "status": response_status,
                **({"mode": repair_mode} if repair_mode else {}),
                "events_collected": stored_count,
                "selected_ticker_count": len(selected),
                "failed_tickers": failed_tickers,
                "zero_event_tickers": zero_event_tickers,
                "provider_health": provider_health,
                "warnings": warnings,
                "reconciliation_counts": reconciliation_counts,
                "conflict_confirmation_attempts": conflict_confirmation_attempts,
                "history_coverage": history_coverage["summary"],
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


def _has_explicit_ticker_selection(event: dict[str, Any]) -> bool:
    return any(key in event for key in ("tickers", "ticker_offset", "max_tickers"))


def _is_full_watchlist_selection(
    stocks: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> bool:
    active_tickers = {str(stock.get("ticker") or "").upper() for stock in stocks}
    selected_tickers = {str(stock.get("ticker") or "").upper() for stock in selected}
    return bool(active_tickers) and selected_tickers == active_tickers


def _collect_per_ticker(
    selected: list[dict[str, Any]],
    event: dict[str, Any],
    log: Any,
    *,
    range_start: date,
    range_end: date,
    provider_events: list[dict[str, Any]],
    provider_attempts: dict[str, dict[str, Any]] | None = None,
    ticker_collection_outcomes: dict[str, str] | None = None,
    include_range_calendar: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    failed_tickers: list[str] = []
    collected_events: list[dict[str, Any]] = []
    if include_range_calendar:
        try:
            collected_events.extend(
                fetch_earnings_calendar_events(
                    selected,
                    start_date=max(range_start, date.today()),
                    end_date=range_end,
                    provider_events=provider_events,
                    provider_attempts=provider_attempts,
                )
            )
        except Exception as exc:
            log.warning("earnings_calendar_range_collection_failed", error=str(exc))

    for stock in selected:
        ticker = stock["ticker"]
        ticker_attempts: dict[str, dict[str, Any]] = {}
        try:
            events = fetch_earnings_events(
                ticker,
                company_name=stock.get("company_name"),
                limit=int(event.get("limit", DEFAULT_LIMIT)),
                start_date=range_start,
                end_date=range_end,
                provider_events=provider_events,
                provider_attempts=ticker_attempts,
            )
            collected_events.extend(events)
            _merge_provider_attempts(provider_attempts, ticker_attempts)
            outcome = _ticker_collection_outcome(events, ticker_attempts)
            if ticker_collection_outcomes is not None:
                ticker_collection_outcomes[ticker] = outcome
            if outcome in INCOMPLETE_COLLECTION_OUTCOMES:
                failed_tickers.append(ticker)
        except Exception as exc:
            failed_tickers.append(ticker)
            if ticker_collection_outcomes is not None:
                ticker_collection_outcomes[ticker] = "failed"
            log.warning("earnings_ticker_collection_failed", ticker=ticker, error=str(exc))
    reconciled_events = reconcile_earnings_events(collected_events)
    enriched_events = [
        enrich_price_reaction(earnings_event)
        for earnings_event in sorted(
            reconciled_events,
            key=lambda item: (item["ticker"], item["event_date"]),
        )
    ]
    return enriched_events, failed_tickers


def _ticker_collection_outcome(
    events: list[dict[str, Any]],
    attempts: dict[str, dict[str, Any]],
) -> str:
    if events:
        return "collected"
    statuses = {
        status
        for attempt in attempts.values()
        for status in attempt.get("statuses", {})
    }
    for outcome in ("budget_exhausted", "rate_limited", "unconfigured", "failed"):
        if outcome in statuses:
            return outcome
    return "empty"


def _merge_provider_attempts(
    target: dict[str, dict[str, Any]] | None,
    source: dict[str, dict[str, Any]],
) -> None:
    if target is None:
        return
    for provider, attempt in source.items():
        merged = target.setdefault(
            provider,
            {"attempt_count": 0, "event_count": 0, "raw_event_count": 0, "statuses": {}},
        )
        for field in ("attempt_count", "event_count", "raw_event_count"):
            merged[field] += int(attempt.get(field, 0))
        for status, count in attempt.get("statuses", {}).items():
            merged["statuses"][status] = merged["statuses"].get(status, 0) + int(count)
        if attempt.get("last_error"):
            merged["last_error"] = attempt["last_error"]


def _select_rotating_fallback_stocks(
    stocks: list[dict[str, Any]],
    event: dict[str, Any],
    collection_date: date,
) -> list[dict[str, Any]]:
    """Select a bounded daily slice without permanently favoring A tickers."""
    ordered = sorted(stocks, key=lambda stock: stock["ticker"])
    if not ordered:
        return []
    limit = min(
        max(int(event.get("fallback_max_tickers", DEFAULT_FALLBACK_MAX_TICKERS)), 0),
        len(ordered),
    )
    if limit <= 0:
        return []
    offset = event.get("fallback_ticker_offset")
    if offset is None:
        offset = (collection_date.toordinal() * limit) % len(ordered)
    start = max(int(offset), 0) % len(ordered)
    return [ordered[(start + index) % len(ordered)] for index in range(limit)]


def _record_provider_attempt(
    attempts: dict[str, dict[str, Any]] | None,
    provider: str,
    status: str,
    *,
    event_count: int = 0,
    raw_event_count: int | None = None,
    error: object | None = None,
) -> None:
    if attempts is None:
        return
    item = attempts.setdefault(
        provider,
        {"attempt_count": 0, "event_count": 0, "raw_event_count": 0, "statuses": {}},
    )
    item["attempt_count"] += 1
    item["event_count"] += event_count
    item["raw_event_count"] += raw_event_count if raw_event_count is not None else event_count
    statuses = item["statuses"]
    statuses[status] = statuses.get(status, 0) + 1
    if error is not None:
        item["last_error"] = _safe_provider_error(error)


def _build_provider_health(
    *,
    selected_ticker_count: int,
    stored_count: int,
    failed_tickers: list[str],
    provider_attempts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if selected_ticker_count <= 0:
        return {"status": "ok", "providers": provider_attempts}
    failed_statuses = {"failed", "rate_limited", "unconfigured", "budget_exhausted"}
    observed_statuses = {
        status
        for attempt in provider_attempts.values()
        for status in attempt.get("statuses", {})
    }
    if stored_count > 0:
        return {
            "status": (
                "partial"
                if failed_tickers or observed_statuses.intersection(failed_statuses)
                else "ok"
            ),
            "providers": provider_attempts,
        }
    reason = (
        "providers_unavailable"
        if observed_statuses.intersection(failed_statuses)
        else "provider_returned_zero_events"
    )
    return {
        "status": "degraded",
        "reason": reason,
        "message": (
            "No earnings events were collected for the active watchlist. "
            "The run used the full-watchlist Finnhub calendar query and a bounded "
            "rotating per-ticker fallback; inspect provider diagnostics before analysis."
        ),
        "providers": provider_attempts,
    }


def _calendar_warnings(provider_health: dict[str, Any]) -> list[str]:
    if provider_health.get("status") == "degraded":
        return [str(provider_health.get("message") or provider_health.get("reason"))]
    if provider_health.get("status") == "partial":
        return ["Earnings events were collected, but one or more providers failed or were unavailable."]
    return []


def _response_status(
    failed_tickers: list[str],
    provider_health: dict[str, Any],
) -> str:
    if provider_health.get("status") == "degraded":
        return "degraded"
    if failed_tickers or provider_health.get("status") == "partial":
        return "partial"
    return "success"


def _reconciliation_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "confirmed": 0,
        "company_confirmed": 0,
        "single_source": 0,
        "conflicting": 0,
        "unreconciled": 0,
    }
    for event in events:
        status = str(event.get("reconciliation_status") or "unreconciled")
        counts[status if status in counts else "unreconciled"] += 1
    return counts


def _store_events(events: list[dict[str, Any]]) -> int:
    stored_count = 0
    for earnings_event in events:
        store.put_earnings_event(earnings_event)
        stored_count += 1
    return stored_count


def _load_stored_history(
    *,
    selected: list[dict[str, Any]],
    range_start: date,
    range_end: date,
    full_watchlist: bool,
) -> list[dict[str, Any]]:
    if range_start > range_end:
        return []
    if full_watchlist:
        return store.earnings_events(range_start, range_end)
    events: list[dict[str, Any]] = []
    for stock in selected:
        events.extend(
            store.earnings_events_for_ticker(
                stock["ticker"],
                range_start,
                range_end,
            )
        )
    return events


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

    manifest_date = date.fromisoformat(
        str(event.get("manifest_date") or manifest_date_from_key(key))
    )
    task = get_persisted_manifest_task(manifest_date, task_id)
    if task.task_type != CollectionTaskType.EARNINGS:
        raise ValueError(f"Task {task_id} is not an earnings collection task")
    event["tickers"] = task.tickers
    event["max_tickers"] = len(task.tickers)
    lease_owner = getattr(context, "aws_request_id", None) if context else None
    mark_persisted_manifest_task_running(
        manifest_date,
        task_id,
        lease_owner=lease_owner,
    )
    return ManifestTaskRun(
        bucket=bucket,
        key=key,
        manifest_date=manifest_date,
        task_id=task_id,
    )


def _complete_manifest_task_run(
    task_run: ManifestTaskRun,
    selected_ticker_count: int,
    stored_count: int,
    failed_tickers: list[str],
    provider_health: dict[str, Any],
    ticker_collection_outcomes: dict[str, str] | None = None,
) -> None:
    try:
        failed_count = len(set(failed_tickers))
        provider_degraded = provider_health.get("status") == "degraded"
        failure_reason = None
        if provider_degraded:
            failure_reason = str(
                provider_health.get("reason") or "earnings_provider_degraded"
            )
        elif failed_count:
            failure_reason = _manifest_ticker_failure_reason(
                ticker_collection_outcomes or {}
            )
        output_counts = CollectionOutputCounts(
            records_fetched=stored_count,
            records_written=stored_count,
            failed_records=failed_count,
            successful_tickers=(
                0
                if provider_degraded
                else max(selected_ticker_count - failed_count, 0)
            ),
            failed_tickers=(selected_ticker_count if provider_degraded else failed_count),
        )
        complete_persisted_manifest_task(
            task_run.manifest_date,
            task_run.task_id,
            output_counts,
            failed=failed_count > 0 or provider_degraded,
            failure_reason=failure_reason,
        )
    except Exception as exc:
        logger.warning(
            "earnings_manifest_task_completion_failed",
            task_id=task_run.task_id,
            error=str(exc),
        )


def _manifest_ticker_failure_reason(collection_outcomes: dict[str, str]) -> str:
    outcomes = set(collection_outcomes.values())
    if "rate_limited" in outcomes:
        return "alpha_vantage quota or rate limit exhausted"
    if "budget_exhausted" in outcomes:
        return "alpha_vantage invocation budget exhausted"
    if "unconfigured" in outcomes:
        return "earnings provider unconfigured"
    return "partial_ticker_failure"


def _fail_manifest_task_run(task_run: ManifestTaskRun, reason: str) -> None:
    try:
        complete_persisted_manifest_task(
            task_run.manifest_date,
            task_run.task_id,
            CollectionOutputCounts(),
            failed=True,
            failure_reason=reason,
        )
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
    start_date: date | None = None,
    end_date: date | None = None,
    provider_events: list[dict[str, Any]] | None = None,
    provider_attempts: dict[str, dict[str, Any]] | None = None,
    fallback_to_alpha_vantage: bool = True,
) -> list[dict[str, Any]]:
    """Fetch and normalize earnings calendar rows from yfinance."""
    try:
        yf_ticker = yf.Ticker(ticker)
        if hasattr(yf_ticker, "get_earnings_dates"):
            data = yf_ticker.get_earnings_dates(limit=limit)
        else:
            data = getattr(yf_ticker, "earnings_dates", None)
    except Exception as exc:
        _record_provider_attempt(
            provider_attempts,
            "yfinance",
            "failed",
            error=exc,
        )
        logger.warning(
            "yfinance_earnings_events_unavailable",
            ticker=ticker.upper(),
            error=_safe_provider_error(exc),
        )
        data = None
    if data is None or getattr(data, "empty", True):
        if data is not None:
            _record_provider_attempt(provider_attempts, "yfinance", "empty")
        if not fallback_to_alpha_vantage:
            return []
        return fetch_alpha_vantage_earnings_events(
            ticker,
            company_name=company_name,
            start_date=start_date,
            end_date=end_date,
            provider_events=provider_events,
            provider_attempts=provider_attempts,
        )

    today = date.today()
    events: list[dict[str, Any]] = []
    for raw_date, row in data.iterrows():
        event_date = _normalize_event_date(raw_date)
        if event_date is None:
            continue
        if start_date and event_date < start_date:
            continue
        if end_date and event_date > end_date:
            continue
        if provider_events is not None:
            provider_events.append(
                _raw_provider_event(
                    provider="yfinance",
                    ticker=ticker.upper(),
                    company_name=company_name,
                    event_date=event_date,
                    source_url=f"https://finance.yahoo.com/quote/{ticker.upper()}/analysis",
                    raw_fields={
                        str(key): _serialize_raw_value(value)
                        for key, value in row.to_dict().items()
                    },
                )
            )
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
    _record_provider_attempt(
        provider_attempts,
        "yfinance",
        "success" if events else "empty",
        event_count=len(events),
        raw_event_count=len(data.index),
    )
    return events


def fetch_alpha_vantage_earnings_events(
    ticker: str,
    company_name: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    provider_events: list[dict[str, Any]] | None = None,
    provider_attempts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch historical earnings reports from Alpha Vantage."""
    api_key = _alpha_vantage_api_key()
    if not api_key:
        _record_provider_attempt(provider_attempts, "alpha_vantage", "unconfigured")
        logger.warning("alpha_vantage_api_key_not_configured_for_earnings_calendar")
        return []
    if _ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED:
        _record_provider_attempt(provider_attempts, "alpha_vantage", "rate_limited")
        logger.warning(
            "alpha_vantage_earnings_quota_skipped",
            ticker=ticker.upper(),
            reason="quota_exhausted",
        )
        return []
    if _ALPHA_VANTAGE_EARNINGS_CALL_COUNT >= _ALPHA_VANTAGE_EARNINGS_CALL_BUDGET:
        _record_provider_attempt(provider_attempts, "alpha_vantage", "budget_exhausted")
        logger.warning(
            "alpha_vantage_earnings_budget_skipped",
            ticker=ticker.upper(),
            call_budget=_ALPHA_VANTAGE_EARNINGS_CALL_BUDGET,
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
                "function": "EARNINGS",
                "symbol": ticker.upper(),
                "apikey": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        _record_provider_attempt(
            provider_attempts,
            "alpha_vantage",
            "failed",
            error=exc,
        )
        logger.warning(
            "alpha_vantage_earnings_events_unavailable",
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
            "alpha_vantage_earnings_payload_unavailable",
            ticker=ticker.upper(),
            error=_safe_provider_error(provider_error),
        )
        _record_provider_attempt(
            provider_attempts,
            "alpha_vantage",
            "rate_limited" if _is_alpha_vantage_quota_error(provider_error) else "failed",
            error=provider_error,
        )
        return []

    events: list[dict[str, Any]] = []
    seen_dates: set[date] = set()
    for row in payload.get("quarterlyEarnings", []):
        if not isinstance(row, dict):
            continue
        event_date = _normalize_event_date(
            row.get("reportedDate") or row.get("reportDate")
        )
        if event_date is None or event_date in seen_dates:
            continue
        if event_date < range_start or event_date > range_end:
            continue
        seen_dates.add(event_date)
        if provider_events is not None:
            provider_events.append(
                _raw_provider_event(
                    provider="alpha_vantage",
                    ticker=ticker.upper(),
                    company_name=company_name,
                    event_date=event_date,
                    source_url="https://www.alphavantage.co/documentation/#earnings",
                    raw_fields={
                        str(key): _serialize_raw_value(value)
                        for key, value in row.items()
                    },
                )
            )
        events.append(
            {
                "ticker": ticker.upper(),
                "company_name": company_name,
                "event_date": event_date,
                "eps_estimate": _to_decimal(
                    row.get("estimatedEPS") or row.get("epsEstimate")
                ),
                "reported_eps": _to_decimal(
                    row.get("reportedEPS") or row.get("epsActual")
                ),
                "surprise_percent": _to_decimal(
                    row.get("surprisePercentage") or row.get("surprisePercent")
                ),
                "time_of_day": None,
                "is_upcoming": event_date >= today,
                "provider": "alpha_vantage",
                "source_url": "https://www.alphavantage.co/documentation/#earnings",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    logger.info(
        "alpha_vantage_earnings_events_fetched",
        ticker=ticker.upper(),
        count=len(events),
    )
    _record_provider_attempt(
        provider_attempts,
        "alpha_vantage",
        "success" if events else "empty",
        event_count=len(events),
        raw_event_count=len(payload.get("quarterlyEarnings", [])),
    )
    return events


def fetch_alpha_vantage_earnings_calendar_events(
    stocks: list[dict[str, Any]],
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    start_date: date | None = None,
    end_date: date | None = None,
    provider_events: list[dict[str, Any]] | None = None,
    provider_attempts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch the global Alpha Vantage upcoming-earnings CSV in one request."""
    api_key = _alpha_vantage_api_key()
    if not api_key:
        _record_provider_attempt(
            provider_attempts,
            "alpha_vantage_calendar",
            "unconfigured",
        )
        logger.warning(
            "alpha_vantage_api_key_not_configured_for_global_earnings_calendar"
        )
        return []
    if _ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED:
        _record_provider_attempt(
            provider_attempts,
            "alpha_vantage_calendar",
            "rate_limited",
        )
        return []
    if _ALPHA_VANTAGE_EARNINGS_CALL_COUNT >= _ALPHA_VANTAGE_EARNINGS_CALL_BUDGET:
        _record_provider_attempt(
            provider_attempts,
            "alpha_vantage_calendar",
            "budget_exhausted",
        )
        return []

    today = date.today()
    range_start = start_date or today
    range_end = end_date or today + timedelta(days=max(lookahead_days, 1))
    horizon_days = max((range_end - today).days, 1)
    if horizon_days <= 92:
        horizon = "3month"
    elif horizon_days <= 183:
        horizon = "6month"
    else:
        horizon = "12month"
    try:
        _pace_alpha_vantage_request()
        _record_alpha_vantage_call()
        response = requests.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "EARNINGS_CALENDAR",
                "horizon": horizon,
                "apikey": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        status = "rate_limited" if "429" in str(exc) else "failed"
        _record_provider_attempt(
            provider_attempts,
            "alpha_vantage_calendar",
            status,
            error=exc,
        )
        logger.warning(
            "alpha_vantage_global_earnings_calendar_unavailable",
            error=_safe_provider_error(exc),
        )
        return []

    body = response.text.strip()
    provider_error = _alpha_vantage_text_error(body)
    if provider_error:
        if _is_alpha_vantage_quota_error(provider_error):
            _mark_alpha_vantage_quota_exhausted()
        _record_provider_attempt(
            provider_attempts,
            "alpha_vantage_calendar",
            (
                "rate_limited"
                if _is_alpha_vantage_quota_error(provider_error)
                else "failed"
            ),
            error=provider_error,
        )
        return []

    active_by_ticker = {
        str(stock.get("ticker", "")).upper(): stock
        for stock in stocks
        if stock.get("ticker")
    }
    raw_rows = list(csv.DictReader(io.StringIO(body)))
    events: list[dict[str, Any]] = []
    for row in raw_rows:
        ticker = str(row.get("symbol") or "").strip().upper()
        if ticker not in active_by_ticker:
            continue
        event_date = _normalize_event_date(row.get("reportDate"))
        if event_date is None or event_date < range_start or event_date > range_end:
            continue
        stock = active_by_ticker[ticker]
        if provider_events is not None:
            provider_events.append(
                _raw_provider_event(
                    provider="alpha_vantage_calendar",
                    ticker=ticker,
                    company_name=stock.get("company_name") or row.get("name"),
                    event_date=event_date,
                    source_url=ALPHA_VANTAGE_EARNINGS_CALENDAR_SOURCE_URL,
                    raw_fields={
                        str(key): _serialize_raw_value(value)
                        for key, value in row.items()
                    },
                )
            )
        events.append(
            {
                "ticker": ticker,
                "company_name": stock.get("company_name") or row.get("name"),
                "event_date": event_date,
                "eps_estimate": _to_decimal(row.get("estimate")),
                "revenue_estimate": None,
                "reported_eps": None,
                "surprise_percent": None,
                "time_of_day": _normalize_time_of_day(row.get("timeOfTheDay")),
                "fiscal_period_end": _normalize_event_date(
                    row.get("fiscalDateEnding")
                ),
                "fiscal_quarter": _calendar_quarter_label(
                    _normalize_event_date(row.get("fiscalDateEnding"))
                ),
                "is_upcoming": event_date >= today,
                "provider": "alpha_vantage_calendar",
                "provider_observation_id": _provider_observation_id(
                    "alpha_vantage_calendar",
                    ticker,
                    event_date,
                    row.get("fiscalDateEnding"),
                ),
                "source_url": ALPHA_VANTAGE_EARNINGS_CALENDAR_SOURCE_URL,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    _record_provider_attempt(
        provider_attempts,
        "alpha_vantage_calendar",
        "success" if events else "empty",
        event_count=len(events),
        raw_event_count=len(raw_rows),
    )
    logger.info(
        "alpha_vantage_global_earnings_calendar_fetched",
        count=len(events),
        raw_event_count=len(raw_rows),
        horizon=horizon,
    )
    return events


def fetch_earnings_calendar_events(
    stocks: list[dict[str, Any]],
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    start_date: date | None = None,
    end_date: date | None = None,
    provider_events: list[dict[str, Any]] | None = None,
    provider_attempts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch upcoming earnings from global providers, filtered to watchlist."""
    today = date.today()
    range_start = start_date or today
    range_end = end_date or today + timedelta(days=max(lookahead_days, 1))
    alpha_events = fetch_alpha_vantage_earnings_calendar_events(
        stocks,
        lookahead_days=lookahead_days,
        start_date=range_start,
        end_date=range_end,
        provider_events=provider_events,
        provider_attempts=provider_attempts,
    )
    api_key = _finnhub_api_key()
    if not api_key:
        _record_provider_attempt(provider_attempts, "finnhub", "unconfigured")
        logger.warning("finnhub_api_key_not_configured_for_earnings_calendar")
        return reconcile_earnings_events(alpha_events)

    active_by_ticker = {
        str(stock.get("ticker", "")).upper(): stock
        for stock in stocks
        if stock.get("ticker")
    }
    events: list[dict[str, Any]] = []
    for diagnostic_provider, query_start, query_end in _finnhub_query_ranges(
        range_start, range_end
    ):
        raw_rows = _fetch_finnhub_calendar_rows(
            query_start,
            query_end,
            api_key,
            diagnostic_provider=diagnostic_provider,
            provider_attempts=provider_attempts,
        )
        if raw_rows is None:
            continue
        range_events = _normalize_finnhub_calendar_rows(
            raw_rows,
            active_by_ticker,
            today=today,
            range_start=query_start,
            range_end=query_end,
            provider_events=provider_events,
        )
        events.extend(range_events)
        _record_provider_attempt(
            provider_attempts,
            diagnostic_provider,
            "success" if range_events else "empty",
            event_count=len(range_events),
            raw_event_count=len(raw_rows),
        )
    events = _deduplicate_provider_events(events)
    events = reconcile_earnings_events([*events, *alpha_events])
    events = confirm_near_term_earnings_conflicts(
        events,
        stocks,
        as_of=today,
        provider_events=provider_events,
        provider_attempts=provider_attempts,
    )
    logger.info("earnings_calendar_events_fetched", count=len(events))
    return events


def _finnhub_query_ranges(
    range_start: date,
    range_end: date,
) -> list[tuple[str, date, date]]:
    """Reserve one Finnhub request for the first seven days to avoid row caps."""
    near_end = min(range_end, range_start + timedelta(days=7))
    ranges = [("finnhub", range_start, near_end)]
    later_start = near_end + timedelta(days=1)
    if later_start <= range_end:
        ranges.append(("finnhub_long_range", later_start, range_end))
    return ranges


def _fetch_finnhub_calendar_rows(
    range_start: date,
    range_end: date,
    api_key: str,
    *,
    diagnostic_provider: str,
    provider_attempts: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    try:
        response = requests.get(
            FINNHUB_EARNINGS_CALENDAR_URL,
            params={
                "from": range_start.isoformat(),
                "to": range_end.isoformat(),
                "token": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        status = "rate_limited" if "429" in str(exc) else "failed"
        _record_provider_attempt(
            provider_attempts,
            diagnostic_provider,
            status,
            error=exc,
        )
        logger.warning(
            "finnhub_earnings_calendar_unavailable",
            diagnostic_provider=diagnostic_provider,
            range_start=range_start.isoformat(),
            range_end=range_end.isoformat(),
            error=_safe_provider_error(exc),
        )
        return None
    raw_rows = payload.get("earningsCalendar", [])
    if not isinstance(raw_rows, list):
        _record_provider_attempt(
            provider_attempts,
            diagnostic_provider,
            "failed",
            error="earningsCalendar payload was not a list",
        )
        return None
    return raw_rows


def _normalize_finnhub_calendar_rows(
    raw_rows: list[dict[str, Any]],
    active_by_ticker: dict[str, dict[str, Any]],
    *,
    today: date,
    range_start: date,
    range_end: date,
    provider_events: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in raw_rows:
        ticker = str(row.get("symbol", "")).upper()
        if ticker not in active_by_ticker:
            continue
        event_date = _normalize_event_date(row.get("date"))
        if event_date is None or not range_start <= event_date <= range_end:
            continue
        stock = active_by_ticker[ticker]
        if provider_events is not None:
            provider_events.append(
                _raw_provider_event(
                    provider="finnhub",
                    ticker=ticker,
                    company_name=stock.get("company_name"),
                    event_date=event_date,
                    source_url="https://finnhub.io/calendar/earnings",
                    raw_fields={
                        str(key): _serialize_raw_value(value)
                        for key, value in row.items()
                    },
                )
            )
        fiscal_quarter = _finnhub_fiscal_quarter(row)
        events.append(
            {
                "ticker": ticker,
                "company_name": stock.get("company_name"),
                "event_date": event_date,
                "eps_estimate": _to_decimal(row.get("epsEstimate")),
                "revenue_estimate": _to_decimal(row.get("revenueEstimate")),
                "reported_eps": _to_decimal(row.get("epsActual")),
                "surprise_percent": _to_decimal(row.get("surprisePercent")),
                "time_of_day": _normalize_time_of_day(row.get("hour")),
                "fiscal_period_end": None,
                "fiscal_quarter": fiscal_quarter,
                "is_upcoming": event_date >= today,
                "provider": "finnhub",
                "provider_observation_id": _provider_observation_id(
                    "finnhub", ticker, event_date, fiscal_quarter
                ),
                "source_url": "https://finnhub.io/calendar/earnings",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return events


def confirm_near_term_earnings_conflicts(
    events: list[dict[str, Any]],
    stocks: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    horizon_days: int = DEFAULT_CONFLICT_CONFIRMATION_HORIZON_DAYS,
    max_tickers: int = DEFAULT_CONFLICT_CONFIRMATION_MAX_TICKERS,
    provider_events: list[dict[str, Any]] | None = None,
    provider_attempts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Use a bounded yfinance query only for conflicts inside the near horizon."""
    reconciled = reconcile_earnings_events(events)
    confirmation_start = as_of or date.today()
    confirmation_end = confirmation_start + timedelta(days=max(horizon_days, 0))
    conflict_tickers = sorted(
        {
            str(event.get("ticker") or "").upper()
            for event in reconciled
            if event.get("reconciliation_status") == "conflicting"
            and confirmation_start <= event["event_date"] <= confirmation_end
        }
    )
    if not conflict_tickers or max_tickers <= 0:
        return reconciled

    stock_by_ticker = {
        str(stock.get("ticker") or "").upper(): stock
        for stock in stocks
        if stock.get("ticker")
    }
    attempted_tickers: set[str] = set()
    confirmation_events: list[dict[str, Any]] = []
    for ticker in conflict_tickers[:max_tickers]:
        attempted_tickers.add(ticker)
        stock = stock_by_ticker.get(ticker, {})
        ticker_confirmation_events = fetch_earnings_events(
            ticker,
            company_name=stock.get("company_name"),
            limit=DEFAULT_LIMIT,
            start_date=confirmation_start,
            end_date=confirmation_end,
            provider_events=provider_events,
            provider_attempts=provider_attempts,
            fallback_to_alpha_vantage=False,
        )
        confirmation_events.extend(ticker_confirmation_events)
        _record_provider_attempt(
            provider_attempts,
            "yfinance_conflict_confirmation",
            "success" if ticker_confirmation_events else "empty",
            event_count=len(ticker_confirmation_events),
        )

    confirmed_dates = {
        (str(event.get("ticker") or "").upper(), event["event_date"])
        for event in confirmation_events
        if event.get("provider") == "yfinance"
    }
    reconciled = reconcile_earnings_events([*reconciled, *confirmation_events])
    for event in reconciled:
        ticker = str(event.get("ticker") or "").upper()
        if ticker not in attempted_tickers or event.get("reconciliation_status") != "conflicting":
            continue
        event["confirmation_providers"] = ["yfinance"]
        event["confirmation_status"] = (
            "candidate_supported"
            if (ticker, event["event_date"]) in confirmed_dates
            else "unresolved"
        )
    logger.info(
        "earnings_near_term_conflict_confirmation_completed",
        attempted_ticker_count=len(attempted_tickers),
        confirmation_event_count=len(confirmation_events),
        confirmation_start=confirmation_start.isoformat(),
        confirmation_end=confirmation_end.isoformat(),
    )
    return reconciled


def reconcile_earnings_events(
    events: list[dict[str, Any]],
    *,
    conflict_window_days: int = DEFAULT_EARNINGS_CONFLICT_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Reconcile provider rows without choosing a silent winner for conflicts."""
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        ticker = str(event.get("ticker") or "").upper()
        event_date = _normalize_event_date(event.get("event_date"))
        if not ticker or event_date is None:
            continue
        normalized = {**event, "ticker": ticker, "event_date": event_date}
        normalized.setdefault(
            "provider_observation_id",
            _provider_observation_id(
                str(normalized.get("provider") or "unknown"),
                ticker,
                event_date,
                normalized.get("fiscal_period_end")
                or normalized.get("fiscal_quarter"),
            ),
        )
        by_ticker.setdefault(ticker, []).append(normalized)

    reconciled: list[dict[str, Any]] = []
    window = timedelta(days=max(conflict_window_days, 0))
    for ticker, ticker_events in sorted(by_ticker.items()):
        deduplicated = _deduplicate_provider_events(ticker_events)
        clusters: list[list[dict[str, Any]]] = []
        for event in sorted(deduplicated, key=lambda item: item["event_date"]):
            if (
                not clusters
                or event["event_date"] - clusters[-1][-1]["event_date"] > window
            ):
                clusters.append([event])
            else:
                clusters[-1].append(event)
        for cluster in clusters:
            dates = sorted({event["event_date"] for event in cluster})
            providers = {str(event.get("provider") or "unknown") for event in cluster}
            observation_ids = sorted(
                {
                    observation_id
                    for event in cluster
                    for observation_id in _event_observation_ids(event)
                }
            )
            if len(dates) == 1:
                previously_confirmed = any(
                    event.get("reconciliation_status")
                    in {"confirmed", "company_confirmed"}
                    for event in cluster
                )
                status = (
                    "confirmed"
                    if len(providers) > 1 or previously_confirmed
                    else "single_source"
                )
                confidence = "high" if status == "confirmed" else "low"
                canonical = _merge_exact_date_events(cluster)
                reconciled.append(
                    {
                        **canonical,
                        "canonical_event_id": f"{ticker}:{dates[0].isoformat()}",
                        "reconciliation_status": status,
                        "date_confidence": confidence,
                        "candidate_dates": dates,
                        "observation_ids": observation_ids,
                    }
                )
                continue
            if len(providers) > 1:
                date_identity = ":".join(item.isoformat() for item in dates)
                conflict_id = f"{ticker}:conflict:{date_identity}"
                events_by_date: dict[date, list[dict[str, Any]]] = {}
                for event in cluster:
                    events_by_date.setdefault(event["event_date"], []).append(event)
                for candidate_date in dates:
                    event = _merge_exact_date_events(events_by_date[candidate_date])
                    reconciled.append(
                        {
                            **event,
                            "canonical_event_id": conflict_id,
                            "reconciliation_status": "conflicting",
                            "date_confidence": "conflicting",
                            "candidate_dates": dates,
                            "observation_ids": observation_ids,
                        }
                    )
                continue
            for event in cluster:
                reconciled.append(
                    {
                        **event,
                        "canonical_event_id": (
                            f"{ticker}:{event['event_date'].isoformat()}"
                        ),
                        "reconciliation_status": "single_source",
                        "date_confidence": "low",
                        "candidate_dates": [event["event_date"]],
                        "observation_ids": _event_observation_ids(event),
                    }
                )
    return sorted(
        reconciled,
        key=lambda item: (
            item["ticker"],
            item["event_date"],
            str(item.get("provider") or ""),
        ),
    )


def _deduplicate_provider_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, date], dict[str, Any]] = {}
    for event in events:
        key = (str(event.get("provider") or "unknown"), event["event_date"])
        existing = by_key.get(key)
        if existing is None or _event_completeness(event) > _event_completeness(existing):
            by_key[key] = event
    return list(by_key.values())


def _event_completeness(event: dict[str, Any]) -> int:
    return sum(
        event.get(field) is not None
        for field in (
            "fiscal_period_end",
            "fiscal_quarter",
            "eps_estimate",
            "revenue_estimate",
            "time_of_day",
        )
    )


def _event_observation_ids(event: dict[str, Any]) -> list[str]:
    identifiers = {
        str(value)
        for value in event.get("observation_ids", [])
        if str(value).strip()
    }
    provider_id = str(event.get("provider_observation_id") or "").strip()
    if provider_id:
        identifiers.add(provider_id)
    return sorted(identifiers)


def _merge_exact_date_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        events,
        key=lambda event: (
            0 if event.get("provider") == "finnhub" else 1,
            -_event_completeness(event),
        ),
    )
    merged = dict(ordered[0])
    for event in ordered[1:]:
        for field in (
            "company_name",
            "fiscal_period_end",
            "fiscal_quarter",
            "eps_estimate",
            "revenue_estimate",
            "reported_eps",
            "surprise_percent",
            "time_of_day",
        ):
            if merged.get(field) is None and event.get(field) is not None:
                merged[field] = event[field]
    return merged


def _provider_observation_id(
    provider: str,
    ticker: str,
    event_date: date,
    fiscal_identity: object | None,
) -> str:
    identity = str(fiscal_identity or "unknown").strip().replace(" ", "-")
    return f"{provider}:{ticker}:{event_date.isoformat()}:{identity}"


def _calendar_quarter_label(value: date | None) -> str | None:
    if value is None:
        return None
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _finnhub_fiscal_quarter(row: dict[str, Any]) -> str | None:
    year = row.get("year")
    quarter = row.get("quarter")
    if year in (None, "") or quarter in (None, ""):
        return None
    try:
        return f"{int(year)}-Q{int(quarter)}"
    except (TypeError, ValueError):
        return None


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
        logger.info("alpha_vantage_earnings_request_paced", wait_seconds=wait_seconds)
        time.sleep(wait_seconds)
    _LAST_ALPHA_VANTAGE_REQUEST_AT = time.monotonic()


def _reset_alpha_vantage_invocation_state(event: dict[str, Any]) -> None:
    global _ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED
    global _ALPHA_VANTAGE_EARNINGS_CALL_COUNT
    global _ALPHA_VANTAGE_EARNINGS_CALL_BUDGET
    _ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED = False
    _ALPHA_VANTAGE_EARNINGS_CALL_COUNT = 0
    _ALPHA_VANTAGE_EARNINGS_CALL_BUDGET = max(
        int(
            event.get(
                "alpha_vantage_max_calls",
                DEFAULT_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION,
            )
        ),
        0,
    )


def _record_alpha_vantage_call() -> None:
    global _ALPHA_VANTAGE_EARNINGS_CALL_COUNT
    _ALPHA_VANTAGE_EARNINGS_CALL_COUNT += 1


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
    global _ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED
    _ALPHA_VANTAGE_EARNINGS_QUOTA_EXHAUSTED = True


def _alpha_vantage_payload_error(payload: dict[str, Any]) -> str | None:
    for key in ("Error Message", "Note", "Information"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _alpha_vantage_text_error(body: str) -> str | None:
    stripped = body.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return "Alpha Vantage returned malformed JSON instead of calendar CSV"
    if not isinstance(payload, dict):
        return "Alpha Vantage returned unexpected JSON instead of calendar CSV"
    return _alpha_vantage_payload_error(payload) or (
        "Alpha Vantage returned JSON instead of calendar CSV"
    )


def _raw_provider_event(
    *,
    provider: str,
    ticker: str,
    company_name: str | None,
    event_date: date,
    source_url: str,
    raw_fields: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": provider,
        "ticker": ticker,
        "company_name": company_name,
        "event_date": event_date,
        "source_url": source_url,
        "raw_fields": raw_fields,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


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
    if "before" in text or "pre-market" in text or "bmo" in text:
        return "before_market"
    if "after" in text or "post-market" in text or "amc" in text:
        return "after_market"
    return None


def _row_value(row: Any, column: str) -> Any:
    try:
        return row.get(column)
    except AttributeError:
        return None


def _serialize_raw_value(value: Any) -> Any:
    if value is None:
        return None
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
