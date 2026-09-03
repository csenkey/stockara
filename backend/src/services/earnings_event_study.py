"""Leakage-safe trading-session mapping for earnings event studies."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import date

from src.models.schemas import EarningsSessionMapping


def map_earnings_event_session(
    *,
    report_date: date,
    time_of_day: str | None,
    trading_sessions: Iterable[date],
) -> EarningsSessionMapping:
    """Map an announcement to the first session containing its price reaction.

    The supplied sessions are the authoritative exchange/price calendar. Using
    actual sessions makes weekends, exchange holidays, and unscheduled closures
    explicit without guessing from weekdays.
    """
    sessions = sorted(set(trading_sessions))
    timing = _normalized_timing(time_of_day)
    on_or_after_index = bisect_left(sessions, report_date)
    after_index = bisect_right(sessions, report_date)
    on_or_after = _at(sessions, on_or_after_index)
    after = _at(sessions, after_index)
    report_is_session = on_or_after == report_date

    if timing == "before_market":
        return _resolved_mapping(
            report_date=report_date,
            timing=timing,
            sessions=sessions,
            event_session=on_or_after,
            report_is_session=report_is_session,
        )

    if timing == "after_market":
        return _resolved_mapping(
            report_date=report_date,
            timing=timing,
            sessions=sessions,
            event_session=after if report_is_session else on_or_after,
            report_is_session=report_is_session,
        )

    candidates = _unique_sessions(
        [on_or_after, after if report_is_session else on_or_after]
    )
    if not candidates:
        return EarningsSessionMapping(
            report_date=report_date,
            reported_timing=timing,
            mapping_status="missing_sessions",
            evidence_quality="insufficient",
            warnings=["no_trading_session_on_or_after_report_date"],
        )
    if len(candidates) == 1:
        event_session = candidates[0]
        return EarningsSessionMapping(
            report_date=report_date,
            reported_timing=timing,
            mapping_status="inferred_non_trading_day",
            evidence_quality="medium",
            event_session=event_session,
            prior_session=_prior_session(sessions, event_session),
            candidate_event_sessions=candidates,
            warnings=["report_timing_unknown_but_non_trading_day_collapses_boundary"],
        )
    return EarningsSessionMapping(
        report_date=report_date,
        reported_timing=timing,
        mapping_status="ambiguous",
        evidence_quality="low",
        prior_session=_prior_session(sessions, candidates[0]),
        candidate_event_sessions=candidates,
        warnings=["report_timing_unknown_event_session_not_selected"],
    )


def _resolved_mapping(
    *,
    report_date: date,
    timing: str,
    sessions: list[date],
    event_session: date | None,
    report_is_session: bool,
) -> EarningsSessionMapping:
    if event_session is None:
        return EarningsSessionMapping(
            report_date=report_date,
            reported_timing=timing,
            mapping_status="missing_sessions",
            evidence_quality="insufficient",
            warnings=["no_trading_session_available_for_report_boundary"],
        )
    inferred = not report_is_session
    return EarningsSessionMapping(
        report_date=report_date,
        reported_timing=timing,
        mapping_status="inferred_non_trading_day" if inferred else "exact",
        evidence_quality="medium" if inferred else "high",
        event_session=event_session,
        prior_session=_prior_session(sessions, event_session),
        candidate_event_sessions=[event_session],
        warnings=(["report_date_is_not_a_trading_session"] if inferred else []),
    )


def _normalized_timing(value: str | None) -> str:
    return value if value in {"before_market", "after_market"} else "unknown"


def _prior_session(sessions: list[date], event_session: date) -> date | None:
    index = bisect_left(sessions, event_session) - 1
    return _at(sessions, index) if index >= 0 else None


def _at(sessions: list[date], index: int) -> date | None:
    return sessions[index] if 0 <= index < len(sessions) else None


def _unique_sessions(values: list[date | None]) -> list[date]:
    return sorted({value for value in values if value is not None})
