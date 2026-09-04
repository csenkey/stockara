"""Tests for independent earnings-reaction reconciliation."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.services.earnings_reaction_reconciliation import (
    _comparison_checks,
    reconcile_earnings_reaction,
)


def _rows() -> list[dict]:
    start = date(2024, 6, 24)
    return [
        {
            "trading_date": start + timedelta(days=index),
            "adjusted_close_price": Decimal(200 - index),
            "volume": Decimal(1000 + index * 10),
        }
        for index in range(70)
    ]


def _result() -> dict:
    return reconcile_earnings_reaction(
        ticker="AAPL",
        report_date=date(2024, 8, 1),
        time_of_day="after_market",
        timing_evidence_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019324000080/aapl-20240801.htm"
        ),
        timing_evidence_timestamp=datetime(
            2024, 8, 1, 20, 30, 26, tzinfo=timezone.utc
        ),
        stock_rows=_rows(),
        verified_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )


def test_reconciliation_independently_matches_sessions_returns_and_volume():
    result = _result()

    assert result["status"] == "passed"
    assert result["reference"]["event_session"] == "2024-08-02"
    assert result["actual_reaction"]["event_session"] == "2024-08-02"
    assert len(result["checks"]) == 12
    assert all(check["passed"] for check in result["checks"])
    assert any(
        Decimal(check["expected"]) < 0
        for check in result["checks"]
        if check["name"].endswith(":raw_return_percent")
    )


def test_comparison_fails_when_engine_return_differs_from_reference():
    result = _result()
    actual = deepcopy(result["actual_reaction"])
    actual["windows"][0]["raw_return_percent"] = "99.0000"

    checks = _comparison_checks(
        actual=actual,
        reference=result["reference"],
        tolerance=Decimal("0.0001"),
    )

    failed = [check for check in checks if not check["passed"]]
    assert [check["name"] for check in failed] == [
        "[-5,-1]:raw_return_percent"
    ]


def test_reconciliation_rejects_untraceable_or_mismatched_timing_evidence():
    with pytest.raises(ValueError, match="HTTPS"):
        reconcile_earnings_reaction(
            ticker="AAPL",
            report_date=date(2024, 8, 1),
            time_of_day="after_market",
            timing_evidence_url="http://example.test/evidence",
            timing_evidence_timestamp=datetime(
                2024, 8, 1, 20, 30, tzinfo=timezone.utc
            ),
            stock_rows=_rows(),
        )

    with pytest.raises(ValueError, match="match the report date"):
        reconcile_earnings_reaction(
            ticker="AAPL",
            report_date=date(2024, 8, 1),
            time_of_day="after_market",
            timing_evidence_url="https://example.test/evidence",
            timing_evidence_timestamp=datetime(
                2024, 8, 2, 0, 30, tzinfo=timezone.utc
            ),
            stock_rows=_rows(),
        )
