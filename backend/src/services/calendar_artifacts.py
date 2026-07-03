"""S3 calendar artifact helpers for earnings and dividend events."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import structlog

from src.services.static_artifacts import publish_json_artifact

logger = structlog.get_logger(__name__)


def publish_calendar_artifacts(
    *,
    bucket: str,
    event_type: str,
    events: list[dict[str, Any]],
    collection_date: date,
    range_start: date,
    range_end: date,
    selected_tickers: list[str],
) -> None:
    """Publish normalized calendar events to stable S3 paths."""
    if not bucket:
        return
    normalized_events = [_jsonable_event(event) for event in events]
    payload = {
        "event_type": event_type,
        "collection_date": collection_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "selected_ticker_count": len(selected_tickers),
        "selected_tickers": sorted(set(selected_tickers)),
        "event_count": len(normalized_events),
        "events": normalized_events,
    }
    _safe_publish(
        bucket,
        f"calendar/normalized/{event_type}/collection_date={collection_date.isoformat()}/events.json",
        payload,
    )
    _safe_publish(bucket, f"calendar/normalized/{event_type}/latest.json", payload)
    for ticker, ticker_events in _events_by_ticker(normalized_events).items():
        ticker_payload = {
            **payload,
            "ticker": ticker,
            "event_count": len(ticker_events),
            "events": ticker_events,
        }
        _safe_publish(bucket, f"calendar/by-ticker/{ticker}/{event_type}.json", ticker_payload)


def _safe_publish(bucket: str, key: str, payload: dict[str, Any]) -> None:
    try:
        publish_json_artifact(bucket, key, payload)
    except Exception as exc:
        logger.warning("calendar_artifact_publish_failed", key=key, error=str(exc))


def _events_by_ticker(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        ticker = str(event.get("ticker") or "").upper()
        if not ticker:
            continue
        grouped.setdefault(ticker, []).append(event)
    return grouped


def _jsonable_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: _jsonable_value(value) for key, value in event.items()}


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value
