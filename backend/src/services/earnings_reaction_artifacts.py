"""Build and publish earnings event-reaction artifacts."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from src.models.schemas import EarningsEventReaction
from src.services.earnings_event_study import EVENT_RETURN_WINDOWS
from src.services.static_artifacts import safe_publish_json_artifact

SUMMARY_QUANTUM = Decimal("0.0001")


def build_earnings_reaction_artifacts(
    reactions: list[EarningsEventReaction],
    *,
    as_of: date,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a compact index plus traceable per-event and per-ticker payloads."""
    generated = generated_at or datetime.now(timezone.utc)
    ordered = sorted(
        reactions,
        key=lambda reaction: (
            reaction.ticker,
            reaction.report_date,
            reaction.event_session or date.max,
        ),
    )
    grouped: dict[str, list[EarningsEventReaction]] = {}
    for reaction in ordered:
        grouped.setdefault(reaction.ticker, []).append(reaction)

    ticker_payloads = {
        ticker: _ticker_payload(
            ticker=ticker,
            reactions=ticker_reactions,
            as_of=as_of,
            generated_at=generated,
        )
        for ticker, ticker_reactions in grouped.items()
    }
    event_payloads = {
        reaction.reaction_id: {
            "schema_version": 1,
            "as_of_date": as_of.isoformat(),
            "generated_at": generated.isoformat(),
            "reaction": reaction.model_dump(mode="json"),
        }
        for reaction in ordered
    }
    quality_counts = Counter(reaction.evidence_quality for reaction in ordered)
    index = {
        "schema_version": 1,
        "as_of_date": as_of.isoformat(),
        "generated_at": generated.isoformat(),
        "event_count": len(ordered),
        "ticker_count": len(grouped),
        "evidence_quality_counts": dict(sorted(quality_counts.items())),
        "ticker_summaries": [
            ticker_payloads[ticker]["summary"] for ticker in sorted(ticker_payloads)
        ],
        "event_ids": [reaction.reaction_id for reaction in ordered],
    }
    return {
        "index": index,
        "events": event_payloads,
        "tickers": ticker_payloads,
    }


def publish_earnings_reaction_artifacts(
    *,
    bucket: str,
    artifacts: dict[str, Any],
    artifact_scope: str | None = None,
    publish_latest: bool = True,
) -> None:
    """Publish dated audit artifacts and optionally stable global paths."""
    if not bucket:
        return
    if artifact_scope and publish_latest:
        raise ValueError("scoped reaction artifacts cannot replace global latest")
    as_of = artifacts["index"]["as_of_date"]
    prefix = f"earnings/reactions/as_of_date={as_of}"
    if artifact_scope:
        prefix = f"{prefix}/task_id={_safe_path_segment(artifact_scope)}"
    _publish_artifact_set(bucket=bucket, prefix=prefix, artifacts=artifacts)
    if publish_latest:
        safe_publish_json_artifact(
            bucket,
            "earnings/reactions/latest.json",
            artifacts["index"],
        )
        _publish_artifact_set(
            bucket=bucket,
            prefix="earnings/reactions/current",
            artifacts=artifacts,
            include_index=False,
        )


def _publish_artifact_set(
    *,
    bucket: str,
    prefix: str,
    artifacts: dict[str, Any],
    include_index: bool = True,
) -> None:
    if include_index:
        safe_publish_json_artifact(bucket, f"{prefix}/index.json", artifacts["index"])
    for reaction_id, payload in artifacts["events"].items():
        ticker = payload["reaction"]["ticker"]
        safe_publish_json_artifact(
            bucket,
            f"{prefix}/events/{ticker}/{_safe_path_segment(reaction_id)}.json",
            payload,
        )
    for ticker, payload in artifacts["tickers"].items():
        safe_publish_json_artifact(
            bucket,
            f"{prefix}/by-ticker/{ticker}.json",
            payload,
        )


def _ticker_payload(
    *,
    ticker: str,
    reactions: list[EarningsEventReaction],
    as_of: date,
    generated_at: datetime,
) -> dict[str, Any]:
    quality_counts = Counter(reaction.evidence_quality for reaction in reactions)
    report_dates = [reaction.report_date for reaction in reactions]
    summary = {
        "ticker": ticker,
        "event_count": len(reactions),
        "oldest_report_date": min(report_dates).isoformat(),
        "newest_report_date": max(report_dates).isoformat(),
        "evidence_quality_counts": dict(sorted(quality_counts.items())),
        "window_statistics": _window_statistics(reactions),
        "average_abnormal_volume_percent": _mean(
            [
                reaction.abnormal_volume_percent
                for reaction in reactions
                if reaction.abnormal_volume_percent is not None
            ]
        ),
        "abnormal_volume_sample_count": sum(
            reaction.abnormal_volume_percent is not None for reaction in reactions
        ),
    }
    return {
        "schema_version": 1,
        "as_of_date": as_of.isoformat(),
        "generated_at": generated_at.isoformat(),
        "summary": _jsonable(summary),
        "reactions": [reaction.model_dump(mode="json") for reaction in reactions],
    }


def _window_statistics(
    reactions: list[EarningsEventReaction],
) -> list[dict[str, Any]]:
    present_windows = {
        window.window for reaction in reactions for window in reaction.windows
    }
    windows = [name for name, _, _ in EVENT_RETURN_WINDOWS if name in present_windows]
    return [
        {
            "window": window_name,
            "raw": _return_statistics(
                _window_values(reactions, window_name, "raw_return_percent")
            ),
            "broad_market_adjusted": _return_statistics(
                _window_values(
                    reactions,
                    window_name,
                    "broad_market_adjusted_return_percent",
                )
            ),
            "sector_adjusted": _return_statistics(
                _window_values(
                    reactions,
                    window_name,
                    "sector_adjusted_return_percent",
                )
            ),
        }
        for window_name in windows
    ]


def _window_values(
    reactions: list[EarningsEventReaction],
    window_name: str,
    field: str,
) -> list[Decimal]:
    values: list[Decimal] = []
    for reaction in reactions:
        for window in reaction.windows:
            value = getattr(window, field) if window.window == window_name else None
            if value is not None:
                values.append(value)
    return values


def _return_statistics(values: list[Decimal]) -> dict[str, Any]:
    return {
        "sample_count": len(values),
        "mean_return_percent": _mean(values),
        "positive_event_percent": (
            (Decimal(sum(value > 0 for value in values)) * Decimal(100) / len(values))
            .quantize(SUMMARY_QUANTUM)
            if values
            else None
        ),
    }


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal(0)) / len(values)).quantize(SUMMARY_QUANTUM)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _safe_path_segment(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in value.strip()
    )
