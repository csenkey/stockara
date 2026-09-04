"""Independent reconciliation for production earnings-reaction calculations.

This module intentionally duplicates the small reference calculation instead of
calling event-study internals.  That separation lets the operator check detect
session-boundary or return-formula regressions in the production engine.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from src.services.earnings_event_study import build_earnings_event_reaction

REFERENCE_WINDOWS = (
    ("[-5,-1]", -5, -1),
    ("[-1,+1]", -1, 1),
    ("[0,+1]", 0, 1),
    ("[+1,+5]", 1, 5),
    ("[+1,+20]", 1, 20),
)
RETURN_QUANTUM = Decimal("0.0001")
DEFAULT_TOLERANCE = Decimal("0.0001")


def reconcile_earnings_reaction(
    *,
    ticker: str,
    report_date: date,
    time_of_day: Literal["before_market", "after_market"],
    timing_evidence_url: str,
    timing_evidence_timestamp: datetime,
    stock_rows: list[dict[str, Any]],
    broad_market_rows: list[dict[str, Any]],
    sector_rows: list[dict[str, Any]],
    sector_benchmark_ticker: str,
    broad_market_ticker: str = "SPY",
    tolerance: Decimal = DEFAULT_TOLERANCE,
    verified_at: datetime | None = None,
) -> dict[str, Any]:
    """Compare the production engine with an independently calculated reference."""
    _validate_inputs(
        report_date=report_date,
        timing_evidence_url=timing_evidence_url,
        timing_evidence_timestamp=timing_evidence_timestamp,
        tolerance=tolerance,
    )
    normalized_rows = _reference_rows(stock_rows)
    normalized_market_rows = _reference_rows(broad_market_rows)
    normalized_sector_rows = _reference_rows(sector_rows)
    sessions = [row["trading_date"] for row in normalized_rows]
    reference = _reference_calculation(
        report_date=report_date,
        time_of_day=time_of_day,
        rows=normalized_rows,
        broad_market_rows=normalized_market_rows,
        sector_rows=normalized_sector_rows,
    )
    actual = build_earnings_event_reaction(
        ticker=ticker,
        report_date=report_date,
        time_of_day=time_of_day,
        stock_rows=stock_rows,
        broad_market_rows=broad_market_rows,
        sector_rows=sector_rows,
        broad_market_ticker=broad_market_ticker,
        sector_benchmark_ticker=sector_benchmark_ticker,
        trading_sessions=sessions,
    )
    checks = _comparison_checks(
        actual=actual.model_dump(mode="json"),
        reference=reference,
        tolerance=tolerance,
    )
    passed = bool(checks) and all(check["passed"] for check in checks)
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "verified_at": (verified_at or datetime.now(timezone.utc)).isoformat(),
        "ticker": ticker.upper(),
        "report_date": report_date.isoformat(),
        "reported_timing": time_of_day,
        "timing_evidence": {
            "source_url": timing_evidence_url,
            "source_timestamp": timing_evidence_timestamp.isoformat(),
        },
        "price_basis": "adjusted_close",
        "broad_market_ticker": broad_market_ticker,
        "sector_benchmark_ticker": sector_benchmark_ticker,
        "tolerance_percentage_points": str(tolerance),
        "stored_price_row_count": len(normalized_rows),
        "stored_broad_market_row_count": len(normalized_market_rows),
        "stored_sector_benchmark_row_count": len(normalized_sector_rows),
        "reference": _jsonable(reference),
        "actual_reaction": actual.model_dump(mode="json"),
        "checks": _jsonable(checks),
    }


def _validate_inputs(
    *,
    report_date: date,
    timing_evidence_url: str,
    timing_evidence_timestamp: datetime,
    tolerance: Decimal,
) -> None:
    if not timing_evidence_url.startswith("https://"):
        raise ValueError("timing evidence must use an HTTPS source URL")
    if timing_evidence_timestamp.tzinfo is None:
        raise ValueError("timing evidence timestamp must include a timezone")
    if timing_evidence_timestamp.date() != report_date:
        raise ValueError("timing evidence timestamp must match the report date")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")


def _reference_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[date, dict[str, Any]] = {}
    for row in rows:
        trading_date = _date_value(row.get("trading_date"))
        adjusted_close = _decimal_value(
            row.get("adjusted_close_price"), require_positive=True
        )
        volume = _decimal_value(row.get("volume"), require_nonnegative=True)
        if trading_date is not None:
            by_date[trading_date] = {
                "trading_date": trading_date,
                "adjusted_close_price": adjusted_close,
                "volume": volume,
            }
    return [by_date[value] for value in sorted(by_date)]


def _reference_calculation(
    *,
    report_date: date,
    time_of_day: Literal["before_market", "after_market"],
    rows: list[dict[str, Any]],
    broad_market_rows: list[dict[str, Any]],
    sector_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    sessions = [row["trading_date"] for row in rows]
    report_index = bisect_left(sessions, report_date)
    report_is_session = (
        report_index < len(sessions) and sessions[report_index] == report_date
    )
    event_index = report_index
    if report_is_session and time_of_day == "after_market":
        event_index += 1
    if event_index >= len(sessions):
        raise ValueError("stored prices do not contain the event session")
    event_session = sessions[event_index]
    if (event_session - report_date).days > 7:
        raise ValueError("stored prices do not contain a nearby event session")

    prices = {
        row["trading_date"]: row["adjusted_close_price"] for row in rows
    }
    broad_market_prices = {
        row["trading_date"]: row["adjusted_close_price"]
        for row in broad_market_rows
    }
    sector_prices = {
        row["trading_date"]: row["adjusted_close_price"] for row in sector_rows
    }
    windows = []
    for name, start_offset, end_offset in REFERENCE_WINDOWS:
        start_index = event_index + start_offset
        end_index = event_index + end_offset
        if start_index < 0 or end_index >= len(sessions):
            raise ValueError(f"stored prices do not cover reconciliation window {name}")
        start_session = sessions[start_index]
        end_session = sessions[end_index]
        start_price = prices[start_session]
        end_price = prices[end_session]
        raw_return = _reference_return(start_price, end_price, name, "stock")
        broad_market_return = _reference_return(
            broad_market_prices.get(start_session),
            broad_market_prices.get(end_session),
            name,
            "broad market",
        )
        sector_return = _reference_return(
            sector_prices.get(start_session),
            sector_prices.get(end_session),
            name,
            "sector benchmark",
        )
        windows.append(
            {
                "window": name,
                "start_session": start_session.isoformat(),
                "end_session": end_session.isoformat(),
                "raw_return_percent": raw_return,
                "broad_market_return_percent": broad_market_return,
                "broad_market_adjusted_return_percent": (
                    raw_return - broad_market_return
                ).quantize(RETURN_QUANTUM),
                "sector_return_percent": sector_return,
                "sector_adjusted_return_percent": (
                    raw_return - sector_return
                ).quantize(RETURN_QUANTUM),
            }
        )

    baseline = rows[event_index - 20 : event_index]
    if len(baseline) != 20 or any(row["volume"] is None for row in baseline):
        raise ValueError("stored prices do not contain a complete 20-session volume baseline")
    event_volume = rows[event_index]["volume"]
    if event_volume is None:
        raise ValueError("event-session volume is missing")
    average_volume = sum(
        (row["volume"] for row in baseline),
        Decimal(0),
    ) / Decimal(20)
    if average_volume <= 0:
        raise ValueError("volume baseline must be positive")
    abnormal_volume = (
        (event_volume / average_volume - Decimal(1)) * Decimal(100)
    ).quantize(RETURN_QUANTUM)
    return {
        "event_session": event_session.isoformat(),
        "windows": windows,
        "abnormal_volume_percent": abnormal_volume,
        "volume_baseline_sessions": 20,
    }


def _reference_return(
    start: Decimal | None,
    end: Decimal | None,
    window: str,
    series_name: str,
) -> Decimal:
    if start is None or end is None:
        raise ValueError(
            f"{series_name} adjusted close is missing for reconciliation window {window}"
        )
    return ((end / start - Decimal(1)) * Decimal(100)).quantize(RETURN_QUANTUM)


def _comparison_checks(
    *,
    actual: dict[str, Any],
    reference: dict[str, Any],
    tolerance: Decimal,
) -> list[dict[str, Any]]:
    checks = [
        {
            "name": "event_session",
            "expected": reference["event_session"],
            "actual": actual.get("event_session"),
            "passed": actual.get("event_session") == reference["event_session"],
        }
    ]
    actual_windows = {window["window"]: window for window in actual.get("windows", [])}
    for expected in reference["windows"]:
        observed = actual_windows.get(expected["window"], {})
        checks.append(
            {
                "name": f"{expected['window']}:sessions",
                "expected": [expected["start_session"], expected["end_session"]],
                "actual": [
                    observed.get("start_session"),
                    observed.get("end_session"),
                ],
                "passed": (
                    observed.get("start_session") == expected["start_session"]
                    and observed.get("end_session") == expected["end_session"]
                ),
            }
        )
        for field in (
            "raw_return_percent",
            "broad_market_return_percent",
            "broad_market_adjusted_return_percent",
            "sector_return_percent",
            "sector_adjusted_return_percent",
        ):
            expected_return = _decimal_value(expected[field])
            if expected_return is None:
                raise ValueError(
                    f"reference {field} is invalid for window {expected['window']}"
                )
            actual_return = _decimal_value(observed.get(field))
            difference = (
                abs(actual_return - expected_return)
                if actual_return is not None
                else None
            )
            checks.append(
                {
                    "name": f"{expected['window']}:{field}",
                    "expected": expected_return,
                    "actual": actual_return,
                    "difference": difference,
                    "passed": difference is not None and difference <= tolerance,
                }
            )
    expected_volume = _decimal_value(reference["abnormal_volume_percent"])
    if expected_volume is None:
        raise ValueError("reference abnormal volume is invalid")
    actual_volume = _decimal_value(actual.get("abnormal_volume_percent"))
    checks.append(
        {
            "name": "abnormal_volume_percent",
            "expected": expected_volume,
            "actual": actual_volume,
            "difference": (
                abs(actual_volume - expected_volume)
                if actual_volume is not None
                else None
            ),
            "passed": (
                actual_volume is not None
                and abs(actual_volume - expected_volume) <= tolerance
            ),
        }
    )
    return checks


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _decimal_value(
    value: Any,
    *,
    require_positive: bool = False,
    require_nonnegative: bool = False,
) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if require_positive and parsed <= 0:
        return None
    if require_nonnegative and parsed < 0:
        return None
    return parsed


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
