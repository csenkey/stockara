"""Tests for timing-aware earnings event-session mapping."""

from datetime import date, timedelta

from decimal import Decimal

from src.services.earnings_event_study import (
    build_earnings_event_reaction,
    map_earnings_event_session,
)


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


def _rows(
    *,
    start_day: int = 1,
    count: int = 50,
    price_multiplier: Decimal = Decimal(1),
) -> list[dict]:
    return [
        {
            "trading_date": date(2026, 1, start_day) + timedelta(days=index),
            "close_price": Decimal(500 + index),
            "adjusted_close_price": Decimal(100 + index) * price_multiplier,
            "volume": 1000 if index != 25 else 2000,
        }
        for index in range(count)
    ]


def test_event_reaction_computes_all_required_adjusted_windows():
    stock_rows = _rows()
    market_rows = _rows(price_multiplier=Decimal(2))
    sector_rows = _rows(price_multiplier=Decimal("1.5"))

    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=stock_rows,
        broad_market_rows=market_rows,
        sector_rows=sector_rows,
        sector_benchmark_ticker="XLK",
    )

    assert [window.window for window in reaction.windows] == [
        "[-5,-1]",
        "[-1,+1]",
        "[0,+1]",
        "[+1,+5]",
        "[+1,+20]",
    ]
    assert all(window.quality == "complete" for window in reaction.windows)
    assert all(
        window.broad_market_adjusted_return_percent == Decimal("0.0000")
        for window in reaction.windows
    )
    assert all(
        window.sector_adjusted_return_percent == Decimal("0.0000")
        for window in reaction.windows
    )
    assert reaction.abnormal_volume_percent == Decimal("100.0000")
    assert reaction.broad_market_ticker == "SPY"
    assert reaction.sector_benchmark_ticker == "XLK"
    assert reaction.volume_baseline_sessions == 20
    assert reaction.evidence_quality == "high"


def test_event_reaction_uses_adjusted_price_not_raw_close_across_split():
    stock_rows = _rows()
    event_index = 25
    stock_rows[event_index - 1]["close_price"] = Decimal(200)
    stock_rows[event_index + 1]["close_price"] = Decimal(105)

    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=stock_rows,
        broad_market_rows=_rows(),
        sector_rows=_rows(),
        sector_benchmark_ticker="XLK",
    )

    window = next(item for item in reaction.windows if item.window == "[-1,+1]")
    assert window.raw_return_percent == Decimal("1.6129")
    assert window.raw_return_percent != Decimal("-47.5000")


def test_missing_benchmarks_preserve_raw_return_and_reduce_quality():
    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=_rows(),
    )

    one_day = next(item for item in reaction.windows if item.window == "[0,+1]")
    assert one_day.raw_return_percent == Decimal("0.8000")
    assert one_day.broad_market_adjusted_return_percent is None
    assert one_day.sector_adjusted_return_percent is None
    assert one_day.quality == "partial"
    assert set(one_day.missing_inputs) == {
        "broad_market_adjusted_close",
        "sector_adjusted_close",
    }
    assert reaction.evidence_quality == "medium"


def test_unnamed_sector_rows_are_not_used_as_untraceable_evidence():
    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=_rows(),
        broad_market_rows=_rows(),
        sector_rows=_rows(),
    )

    one_day = next(item for item in reaction.windows if item.window == "[0,+1]")
    assert one_day.raw_return_percent is not None
    assert one_day.sector_return_percent is None
    assert one_day.sector_adjusted_return_percent is None
    assert "sector_benchmark_identity_missing" in reaction.warnings
    assert reaction.evidence_quality == "medium"


def test_missing_adjusted_stock_price_never_fabricates_zero_return():
    stock_rows = _rows()
    stock_rows[26]["adjusted_close_price"] = None

    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=stock_rows,
        broad_market_rows=_rows(),
        sector_rows=_rows(),
    )

    one_day = next(item for item in reaction.windows if item.window == "[0,+1]")
    assert one_day.raw_return_percent is None
    assert one_day.quality == "missing"
    assert "stock_adjusted_close" in one_day.missing_inputs
    assert reaction.evidence_quality == "low"


def test_incomplete_volume_baseline_is_missing_not_zero():
    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 10),
        time_of_day="before_market",
        stock_rows=_rows(count=40),
        broad_market_rows=_rows(count=40),
        sector_rows=_rows(count=40),
    )

    assert reaction.abnormal_volume_percent is None
    assert reaction.volume_baseline_sessions == 9
    assert "abnormal_volume_baseline_incomplete" in reaction.warnings


def test_missing_stock_session_does_not_shift_event_boundary():
    complete_rows = _rows()
    stock_rows = [row for index, row in enumerate(complete_rows) if index != 25]
    sessions = [row["trading_date"] for row in complete_rows]

    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=stock_rows,
        broad_market_rows=_rows(),
        sector_rows=_rows(),
        trading_sessions=sessions,
    )

    assert reaction.event_session == date(2026, 1, 26)
    one_day = next(item for item in reaction.windows if item.window == "[0,+1]")
    assert one_day.start_session == date(2026, 1, 26)
    assert one_day.raw_return_percent is None
    assert one_day.quality == "missing"


def test_single_benchmark_gap_only_removes_affected_adjustment():
    complete_rows = _rows()
    market_rows = [row for index, row in enumerate(_rows()) if index != 26]

    reaction = build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=complete_rows,
        broad_market_rows=market_rows,
        sector_rows=_rows(),
        sector_benchmark_ticker="XLK",
        trading_sessions=[row["trading_date"] for row in complete_rows],
    )

    one_day = next(item for item in reaction.windows if item.window == "[0,+1]")
    assert one_day.raw_return_percent == Decimal("0.8000")
    assert one_day.broad_market_return_percent is None
    assert one_day.broad_market_adjusted_return_percent is None
    assert one_day.sector_adjusted_return_percent == Decimal("0.0000")
    assert one_day.quality == "partial"


def test_delisted_or_truncated_history_keeps_short_windows_only():
    complete_rows = _rows()
    stock_rows = complete_rows[:31]

    reaction = build_earnings_event_reaction(
        ticker="OLD",
        report_date=date(2026, 1, 26),
        time_of_day="before_market",
        stock_rows=stock_rows,
        broad_market_rows=_rows(),
        sector_rows=_rows(),
        sector_benchmark_ticker="XLK",
        trading_sessions=[row["trading_date"] for row in complete_rows],
    )

    short_window = next(item for item in reaction.windows if item.window == "[+1,+5]")
    long_window = next(item for item in reaction.windows if item.window == "[+1,+20]")
    assert short_window.raw_return_percent is not None
    assert short_window.quality == "complete"
    assert long_window.raw_return_percent is None
    assert long_window.quality == "missing"
    assert reaction.evidence_quality == "low"
