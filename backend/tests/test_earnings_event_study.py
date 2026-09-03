"""Tests for timing-aware earnings event-session mapping."""

from datetime import date

from src.services.earnings_event_study import map_earnings_event_session


MONDAY = date(2026, 8, 31)
TUESDAY = date(2026, 9, 1)
WEDNESDAY = date(2026, 9, 2)


def test_before_market_maps_to_same_trading_session():
    mapping = map_earnings_event_session(
        report_date=MONDAY,
        time_of_day="before_market",
        trading_sessions=[MONDAY, TUESDAY, WEDNESDAY],
    )

    assert mapping.event_session == MONDAY
    assert mapping.mapping_status == "exact"
    assert mapping.evidence_quality == "high"


def test_after_market_maps_to_next_trading_session():
    mapping = map_earnings_event_session(
        report_date=MONDAY,
        time_of_day="after_market",
        trading_sessions=[MONDAY, TUESDAY, WEDNESDAY],
    )

    assert mapping.event_session == TUESDAY
    assert mapping.prior_session == MONDAY
    assert mapping.mapping_status == "exact"


def test_after_market_skips_weekend_and_market_holiday():
    friday = date(2026, 9, 4)
    tuesday_after_holiday = date(2026, 9, 8)
    mapping = map_earnings_event_session(
        report_date=friday,
        time_of_day="after_market",
        trading_sessions=[friday, tuesday_after_holiday],
    )

    assert mapping.event_session == tuesday_after_holiday
    assert mapping.prior_session == friday


def test_weekend_report_maps_to_next_available_session():
    friday = date(2026, 9, 4)
    saturday = date(2026, 9, 5)
    monday = date(2026, 9, 7)

    for timing in ("before_market", "after_market"):
        mapping = map_earnings_event_session(
            report_date=saturday,
            time_of_day=timing,
            trading_sessions=[friday, monday],
        )
        assert mapping.event_session == monday
        assert mapping.mapping_status == "inferred_non_trading_day"
        assert mapping.evidence_quality == "medium"


def test_unknown_timing_on_trading_day_retains_both_candidates():
    mapping = map_earnings_event_session(
        report_date=MONDAY,
        time_of_day=None,
        trading_sessions=[MONDAY, TUESDAY, WEDNESDAY],
    )

    assert mapping.event_session is None
    assert mapping.candidate_event_sessions == [MONDAY, TUESDAY]
    assert mapping.mapping_status == "ambiguous"
    assert mapping.evidence_quality == "low"


def test_unknown_timing_on_weekend_collapses_to_one_boundary():
    mapping = map_earnings_event_session(
        report_date=date(2026, 9, 5),
        time_of_day="unexpected-provider-value",
        trading_sessions=[date(2026, 9, 4), date(2026, 9, 7)],
    )

    assert mapping.event_session == date(2026, 9, 7)
    assert mapping.candidate_event_sessions == [date(2026, 9, 7)]
    assert mapping.mapping_status == "inferred_non_trading_day"
    assert mapping.evidence_quality == "medium"


def test_missing_future_session_is_insufficient_not_zero():
    mapping = map_earnings_event_session(
        report_date=WEDNESDAY,
        time_of_day="after_market",
        trading_sessions=[MONDAY, TUESDAY, WEDNESDAY],
    )

    assert mapping.event_session is None
    assert mapping.mapping_status == "missing_sessions"
    assert mapping.evidence_quality == "insufficient"
    assert mapping.warnings == ["no_trading_session_available_for_report_boundary"]
