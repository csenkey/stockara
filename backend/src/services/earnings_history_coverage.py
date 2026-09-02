"""Build and publish auditable earnings-history coverage reports."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

import structlog

from src.services.static_artifacts import publish_json_artifact

logger = structlog.get_logger(__name__)

REQUIRED_HISTORY_QUARTERS = 8
INCOMPLETE_COLLECTION_OUTCOMES = {
    "budget_exhausted",
    "failed",
    "rate_limited",
    "unconfigured",
}


def build_earnings_history_coverage(
    *,
    tickers: list[str],
    events: list[dict[str, Any]],
    as_of: date,
    collection_outcomes: dict[str, str] | None = None,
    required_quarters: int = REQUIRED_HISTORY_QUARTERS,
) -> dict[str, Any]:
    """Summarize stored historical earnings coverage for every selected ticker."""
    normalized_tickers = sorted({ticker.upper() for ticker in tickers if ticker})
    outcomes = {
        ticker.upper(): outcome
        for ticker, outcome in (collection_outcomes or {}).items()
        if ticker
    }
    events_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        ticker = str(event.get("ticker") or "").upper()
        event_date = _event_date(event.get("event_date"))
        if ticker not in normalized_tickers or event_date is None or event_date >= as_of:
            continue
        events_by_ticker.setdefault(ticker, []).append({**event, "event_date": event_date})

    ticker_rows = [
        _ticker_coverage(
            ticker=ticker,
            events=events_by_ticker.get(ticker, []),
            collection_outcome=outcomes.get(ticker, "not_attempted"),
            required_quarters=required_quarters,
        )
        for ticker in normalized_tickers
    ]
    coverage_counts = Counter(row["coverage_status"] for row in ticker_rows)
    outcome_counts = Counter(row["collection_outcome"] for row in ticker_rows)
    complete_count = coverage_counts["complete"]
    incomplete_collection_count = sum(
        1
        for row in ticker_rows
        if row["collection_outcome"] in INCOMPLETE_COLLECTION_OUTCOMES
    )
    ticker_count = len(ticker_rows)
    return {
        "schema_version": 1,
        "as_of_date": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "required_quarters": required_quarters,
        "ticker_count": ticker_count,
        "audit_status": (
            "complete"
            if complete_count == ticker_count and incomplete_collection_count == 0
            else "incomplete"
        ),
        "summary": {
            "complete_ticker_count": complete_count,
            "partial_ticker_count": coverage_counts["partial"],
            "missing_ticker_count": coverage_counts["missing"],
            "incomplete_ticker_count": ticker_count - complete_count,
            "incomplete_collection_ticker_count": incomplete_collection_count,
            "coverage_percent": (
                round(complete_count * 100 / ticker_count, 2) if ticker_count else 100.0
            ),
            "coverage_status_counts": dict(sorted(coverage_counts.items())),
            "collection_outcome_counts": dict(sorted(outcome_counts.items())),
        },
        "tickers": ticker_rows,
    }


def publish_earnings_history_coverage(
    *,
    bucket: str,
    payload: dict[str, Any],
    artifact_scope: str | None = None,
    publish_latest: bool = True,
) -> None:
    """Publish the report to a dated path and optionally the global latest path."""
    if not bucket:
        return
    as_of = str(payload["as_of_date"])
    prefix = f"earnings/history-coverage/as_of_date={as_of}"
    if artifact_scope:
        prefix = f"{prefix}/task_id={_safe_path_segment(artifact_scope)}"
    _safe_publish(bucket, f"{prefix}/coverage.json", payload)
    if publish_latest:
        _safe_publish(bucket, "earnings/history-coverage/latest.json", payload)


def _ticker_coverage(
    *,
    ticker: str,
    events: list[dict[str, Any]],
    collection_outcome: str,
    required_quarters: int,
) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: event["event_date"])
    quarter_ids = {
        str(event.get("fiscal_quarter") or event["event_date"].isoformat())
        for event in ordered
    }
    quarter_count = len(quarter_ids)
    coverage_status = (
        "complete"
        if quarter_count >= required_quarters
        else "partial" if quarter_count else "missing"
    )
    incomplete_reasons: list[str] = []
    if quarter_count < required_quarters:
        incomplete_reasons.append("insufficient_quarters")
    if collection_outcome in INCOMPLETE_COLLECTION_OUTCOMES:
        incomplete_reasons.append(f"collection_{collection_outcome}")
    return {
        "ticker": ticker,
        "coverage_status": coverage_status,
        "collection_outcome": collection_outcome,
        "historical_event_count": len(ordered),
        "distinct_quarter_count": quarter_count,
        "eps_estimate_count": _present_count(ordered, "eps_estimate"),
        "reported_eps_count": _present_count(ordered, "reported_eps"),
        "revenue_estimate_count": _present_count(ordered, "revenue_estimate"),
        "oldest_event_date": ordered[0]["event_date"].isoformat() if ordered else None,
        "newest_event_date": ordered[-1]["event_date"].isoformat() if ordered else None,
        "incomplete_reasons": incomplete_reasons,
    }


def _present_count(events: list[dict[str, Any]], field: str) -> int:
    return sum(event.get(field) is not None for event in events)


def _event_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _safe_path_segment(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in value.strip()
    )


def _safe_publish(bucket: str, key: str, payload: dict[str, Any]) -> None:
    try:
        publish_json_artifact(bucket, key, payload)
    except Exception as exc:
        logger.warning(
            "earnings_history_coverage_publish_failed",
            key=key,
            error=str(exc),
        )
