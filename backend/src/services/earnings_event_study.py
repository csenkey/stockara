"""Leakage-safe trading-session mapping for earnings event studies."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from src.models.schemas import (
    EarningsEventReaction,
    EarningsReturnWindow,
    EarningsSessionMapping,
)

EVENT_RETURN_WINDOWS = (
    ("[-5,-1]", -5, -1),
    ("[-1,+1]", -1, 1),
    ("[0,+1]", 0, 1),
    ("[+1,+5]", 1, 5),
    ("[+1,+20]", 1, 20),
)
ABNORMAL_VOLUME_BASELINE_SESSIONS = 20
RETURN_QUANTUM = Decimal("0.0001")
MAX_INFERRED_SESSION_GAP = timedelta(days=7)


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
    on_or_after = _near_report_session(report_date, _at(sessions, on_or_after_index))
    after = _near_report_session(report_date, _at(sessions, after_index))
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

    candidates = _unique_sessions([on_or_after, after if report_is_session else on_or_after])
    if not candidates:
        return EarningsSessionMapping(
            report_date=report_date,
            reported_timing=timing,
            mapping_status="missing_sessions",
            evidence_quality="insufficient",
            warnings=["no_trading_session_on_or_after_report_date"],
        )
    if report_is_session and after is None:
        return EarningsSessionMapping(
            report_date=report_date,
            reported_timing=timing,
            mapping_status="missing_sessions",
            evidence_quality="insufficient",
            prior_session=_prior_session(sessions, report_date),
            candidate_event_sessions=candidates,
            warnings=["unknown_timing_after_market_candidate_missing"],
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


def build_earnings_event_reaction(
    *,
    ticker: str,
    report_date: date,
    time_of_day: str | None,
    stock_rows: Iterable[dict[str, Any]],
    broad_market_rows: Iterable[dict[str, Any]] | None = None,
    sector_rows: Iterable[dict[str, Any]] | None = None,
    broad_market_ticker: str = "SPY",
    sector_benchmark_ticker: str | None = None,
    trading_sessions: Iterable[date] | None = None,
) -> EarningsEventReaction:
    """Compute required event windows from split-adjusted, session-aligned data."""
    stock_series = _normalize_rows(stock_rows)
    market_series = _normalize_rows(broad_market_rows or [])
    sector_series = _normalize_rows(sector_rows or [])
    provided_sessions = sorted(set(trading_sessions or []))
    session_calendar = provided_sessions or sorted(
        {
            row["trading_date"]
            for row in (market_series or stock_series)
        }
    )
    mapping = map_earnings_event_session(
        report_date=report_date,
        time_of_day=time_of_day,
        trading_sessions=session_calendar,
    )
    warnings = list(mapping.warnings)
    if mapping.event_session is None:
        return EarningsEventReaction(
            reaction_id=_reaction_id(ticker, report_date, None),
            ticker=ticker,
            report_date=report_date,
            session_mapping=mapping,
            broad_market_ticker=broad_market_ticker,
            sector_benchmark_ticker=sector_benchmark_ticker,
            evidence_quality="insufficient",
            warnings=[*warnings, "event_session_unresolved"],
        )

    stock_prices = _adjusted_price_by_date(stock_series)
    market_prices = _adjusted_price_by_date(market_series)
    sector_prices = (
        _adjusted_price_by_date(sector_series)
        if sector_benchmark_ticker
        else {}
    )
    if sector_series and not sector_benchmark_ticker:
        warnings.append("sector_benchmark_identity_missing")
    event_index = next(
        (
            index for index, session in enumerate(session_calendar)
            if session == mapping.event_session
        ),
        None,
    )
    if event_index is None:
        return EarningsEventReaction(
            reaction_id=_reaction_id(ticker, report_date, mapping.event_session),
            ticker=ticker,
            report_date=report_date,
            event_session=mapping.event_session,
            session_mapping=mapping,
            broad_market_ticker=broad_market_ticker,
            sector_benchmark_ticker=sector_benchmark_ticker,
            evidence_quality="insufficient",
            warnings=[*warnings, "event_session_price_missing"],
        )

    windows = [
        _build_return_window(
            window=window,
            start_offset=start_offset,
            end_offset=end_offset,
            session_calendar=session_calendar,
            event_index=event_index,
            stock_prices=stock_prices,
            market_prices=market_prices,
            sector_prices=sector_prices,
        )
        for window, start_offset, end_offset in EVENT_RETURN_WINDOWS
    ]
    abnormal_volume, baseline_count = _abnormal_volume(
        stock_rows_by_date={row["trading_date"]: row for row in stock_series},
        session_calendar=session_calendar,
        event_index=event_index,
    )
    if abnormal_volume is None:
        warnings.append("abnormal_volume_baseline_incomplete")
    missing_windows = sum(window.quality == "missing" for window in windows)
    partial_windows = sum(window.quality == "partial" for window in windows)
    evidence_quality = _reaction_quality(
        mapping=mapping,
        missing_windows=missing_windows,
        partial_windows=partial_windows,
        abnormal_volume=abnormal_volume,
    )
    return EarningsEventReaction(
        reaction_id=_reaction_id(ticker, report_date, mapping.event_session),
        ticker=ticker,
        report_date=report_date,
        event_session=mapping.event_session,
        session_mapping=mapping,
        broad_market_ticker=broad_market_ticker,
        sector_benchmark_ticker=sector_benchmark_ticker,
        windows=windows,
        abnormal_volume_percent=abnormal_volume,
        volume_baseline_sessions=baseline_count,
        evidence_quality=evidence_quality,
        warnings=sorted(set(warnings)),
    )


def _build_return_window(
    *,
    window: str,
    start_offset: int,
    end_offset: int,
    session_calendar: list[date],
    event_index: int,
    stock_prices: dict[date, Decimal],
    market_prices: dict[date, Decimal],
    sector_prices: dict[date, Decimal],
) -> EarningsReturnWindow:
    start_index = event_index + start_offset
    end_index = event_index + end_offset
    if start_index < 0 or end_index >= len(session_calendar):
        return EarningsReturnWindow(
            window=window,
            start_offset=start_offset,
            end_offset=end_offset,
            quality="missing",
            missing_inputs=["stock_window_sessions"],
        )
    start_session = session_calendar[start_index]
    end_session = session_calendar[end_index]
    stock_return = _return_percent(
        stock_prices.get(start_session),
        stock_prices.get(end_session),
    )
    missing_inputs: list[str] = []
    if stock_return is None:
        missing_inputs.append("stock_adjusted_close")
    market_return = _return_percent(
        market_prices.get(start_session),
        market_prices.get(end_session),
    )
    if market_return is None:
        missing_inputs.append("broad_market_adjusted_close")
    sector_return = _return_percent(
        sector_prices.get(start_session),
        sector_prices.get(end_session),
    )
    if sector_return is None:
        missing_inputs.append("sector_adjusted_close")
    quality = (
        "missing"
        if stock_return is None
        else "complete" if not missing_inputs else "partial"
    )
    return EarningsReturnWindow(
        window=window,
        start_offset=start_offset,
        end_offset=end_offset,
        start_session=start_session,
        end_session=end_session,
        raw_return_percent=stock_return,
        broad_market_return_percent=market_return,
        broad_market_adjusted_return_percent=_difference(stock_return, market_return),
        sector_return_percent=sector_return,
        sector_adjusted_return_percent=_difference(stock_return, sector_return),
        quality=quality,
        missing_inputs=missing_inputs,
    )


def _normalize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[date, dict[str, Any]] = {}
    for row in rows:
        trading_date = _as_date(row.get("trading_date"))
        if trading_date is not None:
            by_date[trading_date] = {**row, "trading_date": trading_date}
    return [by_date[trading_date] for trading_date in sorted(by_date)]


def _adjusted_price_by_date(
    rows: Iterable[dict[str, Any]],
) -> dict[date, Decimal]:
    prices: dict[date, Decimal] = {}
    for row in _normalize_rows(rows):
        price = _adjusted_price(row)
        if price is not None:
            prices[row["trading_date"]] = price
    return prices


def _adjusted_price(row: dict[str, Any]) -> Decimal | None:
    value = row.get("adjusted_close_price")
    if value is None:
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return price if price > 0 else None


def _return_percent(start: Decimal | None, end: Decimal | None) -> Decimal | None:
    if start is None or end is None or start <= 0:
        return None
    return ((end / start - Decimal(1)) * Decimal(100)).quantize(RETURN_QUANTUM)


def _difference(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return (left - right).quantize(RETURN_QUANTUM)


def _abnormal_volume(
    *,
    stock_rows_by_date: dict[date, dict[str, Any]],
    session_calendar: list[date],
    event_index: int,
) -> tuple[Decimal | None, int]:
    baseline_sessions = session_calendar[
        max(0, event_index - ABNORMAL_VOLUME_BASELINE_SESSIONS) : event_index
    ]
    volumes = [
        _volume(stock_rows_by_date.get(session, {}))
        for session in baseline_sessions
    ]
    if len(volumes) != ABNORMAL_VOLUME_BASELINE_SESSIONS or any(
        volume is None for volume in volumes
    ):
        return None, sum(volume is not None for volume in volumes)
    event_volume = _volume(
        stock_rows_by_date.get(session_calendar[event_index], {})
    )
    if event_volume is None:
        return None, len(volumes)
    complete_volumes = [volume for volume in volumes if volume is not None]
    average = sum(complete_volumes, Decimal(0)) / len(complete_volumes)
    if average <= 0:
        return None, len(complete_volumes)
    abnormal = ((event_volume / average - Decimal(1)) * Decimal(100)).quantize(
        RETURN_QUANTUM
    )
    return abnormal, len(complete_volumes)


def _volume(row: dict[str, Any]) -> Decimal | None:
    try:
        value = Decimal(str(row.get("volume")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _reaction_quality(
    *,
    mapping: EarningsSessionMapping,
    missing_windows: int,
    partial_windows: int,
    abnormal_volume: Decimal | None,
) -> str:
    if missing_windows == len(EVENT_RETURN_WINDOWS):
        return "insufficient"
    if missing_windows or mapping.evidence_quality in {"low", "insufficient"}:
        return "low"
    if partial_windows or abnormal_volume is None or mapping.evidence_quality == "medium":
        return "medium"
    return "high"


def _as_date(value: Any) -> date | None:
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


def _reaction_id(
    ticker: str,
    report_date: date,
    event_session: date | None,
) -> str:
    return (
        f"{ticker.upper()}:{report_date.isoformat()}:"
        f"{event_session.isoformat() if event_session else 'unresolved'}"
    )


def _near_report_session(report_date: date, candidate: date | None) -> date | None:
    if candidate is None or candidate - report_date > MAX_INFERRED_SESSION_GAP:
        return None
    return candidate


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
