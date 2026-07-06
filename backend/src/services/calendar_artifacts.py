"""S3 calendar artifact helpers for earnings and dividend events."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
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
    collection_status: str = "success",
    provider_health: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    zero_event_tickers: list[str] | None = None,
    artifact_scope: str | None = None,
    publish_latest: bool = True,
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
        "collection_status": collection_status,
        "provider_health": provider_health or {"status": "ok"},
        "warnings": warnings or [],
        "zero_event_tickers": sorted(set(zero_event_tickers or [])),
        "events": normalized_events,
    }
    collection_prefix = _collection_prefix(
        "normalized",
        event_type,
        collection_date,
        artifact_scope,
    )
    _safe_publish(bucket, f"{collection_prefix}/events.json", payload)
    if publish_latest:
        _safe_publish(bucket, f"calendar/normalized/{event_type}/latest.json", payload)
        for ticker, ticker_events in _events_by_ticker(normalized_events).items():
            ticker_payload = {
                **payload,
                "ticker": ticker,
                "event_count": len(ticker_events),
                "events": ticker_events,
            }
            _safe_publish(
                bucket,
                f"calendar/by-ticker/{ticker}/{event_type}.json",
                ticker_payload,
            )


def publish_calendar_provider_snapshots(
    *,
    bucket: str,
    event_type: str,
    provider_events: list[dict[str, Any]],
    collection_date: date,
    range_start: date,
    range_end: date,
    selected_tickers: list[str],
    artifact_scope: str | None = None,
    publish_latest: bool = True,
) -> None:
    """Publish raw provider calendar responses for audit and backfill debugging."""
    if not bucket:
        return
    for provider, events in _events_by_provider(provider_events).items():
        payload = {
            "event_type": event_type,
            "provider": provider,
            "collection_date": collection_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "range_start": range_start.isoformat(),
            "range_end": range_end.isoformat(),
            "selected_ticker_count": len(selected_tickers),
            "selected_tickers": sorted(set(selected_tickers)),
            "raw_event_count": len(events),
            "raw_events": events,
        }
        collection_prefix = _collection_prefix(
            f"raw/{provider}",
            event_type,
            collection_date,
            artifact_scope,
        )
        _safe_publish(bucket, f"{collection_prefix}/events.json", payload)
        if publish_latest:
            _safe_publish(bucket, f"calendar/raw/{provider}/{event_type}/latest.json", payload)


def _collection_prefix(
    kind: str,
    event_type: str,
    collection_date: date,
    artifact_scope: str | None,
) -> str:
    prefix = f"calendar/{kind}/{event_type}/collection_date={collection_date.isoformat()}"
    if artifact_scope:
        prefix = f"{prefix}/task_id={_safe_path_segment(artifact_scope)}"
    return prefix


def _safe_path_segment(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in value.strip()
    )


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


def _events_by_provider(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        provider = str(event.get("provider") or "unknown").lower()
        grouped.setdefault(provider, []).append(_jsonable_event(event))
    return grouped


def _jsonable_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: _jsonable_value(value) for key, value in event.items()}


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {str(key): _jsonable_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable_value(item) for item in value]
    return value
