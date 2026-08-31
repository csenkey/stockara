"""Tests for score-independent on-demand holding reviews."""

import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.analysis.ai_analyzer import handler
from src.analysis.holding_review import (
    HoldingAction,
    HoldingEvidenceBuilder,
    HoldingReviewEngine,
    HoldingReviewRequest,
    HoldingReviewStatus,
)


def _rows(as_of: date, count: int = 25) -> list[dict]:
    start = as_of - timedelta(days=(count - 1) * 2)
    return [
        {
            "ticker": "FLAT",
            "trading_date": (start + timedelta(days=index * 2)).isoformat(),
            "close_price": Decimal("100") + Decimal(index) / Decimal("10"),
            "volume": 1000 + index,
            "data_provider": "test-provider",
        }
        for index in range(count)
    ]


def _repository(as_of: date) -> Mock:
    repository = Mock()
    repository.get_stock.return_value = {
        "ticker": "FLAT",
        "company_name": "Flat Dividend Corp",
        "sector": "Utilities",
        "is_active": True,
    }
    repository.get_stock_data.return_value = _rows(as_of)
    repository.market_signals_for_ticker.return_value = []
    repository.news_for_ticker.return_value = []
    repository.earnings_events_for_ticker.return_value = []
    repository.dividend_events_for_ticker.return_value = [
        {
            "ticker": "FLAT",
            "ex_dividend_date": (as_of - timedelta(days=270)).isoformat(),
            "dividend_amount": Decimal("1.50"),
            "provider": "test-provider",
        },
        {
            "ticker": "FLAT",
            "ex_dividend_date": (as_of - timedelta(days=180)).isoformat(),
            "dividend_amount": Decimal("1.50"),
            "provider": "test-provider",
        },
        {
            "ticker": "FLAT",
            "ex_dividend_date": (as_of - timedelta(days=90)).isoformat(),
            "dividend_amount": Decimal("1.50"),
            "provider": "test-provider",
        },
        {
            "ticker": "FLAT",
            "ex_dividend_date": as_of.isoformat(),
            "dividend_amount": Decimal("1.50"),
            "provider": "test-provider",
        },
    ]
    return repository


def _response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )


def _decision(action: str = "KEEP_INCOME") -> dict:
    return {
        "schema_version": "1.0",
        "security_recommendation": "HOLD",
        "risk_level": "MEDIUM",
        "confidence_score": 76,
        "security_thesis": "The business and payout remain stable despite limited price growth.",
        "invalidation_criteria": "Reassess if dividend coverage weakens or earnings contract.",
        "portfolio_action": action,
        "holding_role": "income",
        "capital_efficiency": "ADEQUATE",
        "opportunity_cost_assessment": (
            "No comparison set is available to prove a replacement advantage."
        ),
        "dividend_sustainability": "ADEQUATE",
        "reasoning": "The holding contributes income while its price remains broadly stable.",
        "next_review_trigger": "Next earnings or dividend announcement.",
    }


def test_holding_review_always_invokes_ai_without_candidate_score():
    as_of = date(2026, 8, 31)
    repository = _repository(as_of)
    client = Mock()
    client.chat.completions.create.return_value = _response(_decision())
    request = HoldingReviewRequest(
        ticker="flat",
        quantity=10,
        buying_price="100",
        portfolio_total_value="5000",
        objective="income",
        as_of=as_of,
    )

    result = HoldingReviewEngine(repository, client).review(request)

    assert result.status == HoldingReviewStatus.COMPLETED_DEGRADED
    assert result.analysis is not None
    assert result.analysis.security_recommendation.value == "HOLD"
    assert result.analysis.portfolio_action == HoldingAction.KEEP_INCOME
    assert client.chat.completions.create.call_count == 1
    prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "opportunity_score" not in prompt
    assert "negative_score" not in prompt
    assert "replacement_comparison_set" in result.missing_optional_evidence
    repository.put_candidate_score.assert_not_called()
    repository.put_candidate_analysis.assert_not_called()


def test_evidence_builder_calculates_current_yield_and_yield_on_cost():
    as_of = date(2026, 8, 31)
    repository = _repository(as_of)
    snapshot = HoldingEvidenceBuilder(repository).build(
        HoldingReviewRequest(
            ticker="FLAT",
            quantity=10,
            buying_price="80",
            portfolio_total_value="2000",
            objective="income",
            as_of=as_of,
        )
    )

    assert snapshot.metrics["trailing_dividend_per_share"] == Decimal("6.00")
    assert snapshot.metrics["estimated_trailing_dividend_income"] == Decimal("60.00")
    assert snapshot.metrics["estimated_yield_on_cost_percent"] == Decimal("7.50")
    assert snapshot.metrics["estimated_current_dividend_yield_percent"] < Decimal("6")
    assert snapshot.evidence_hash


def test_missing_required_history_blocks_before_ai():
    as_of = date(2026, 8, 31)
    repository = _repository(as_of)
    repository.get_stock_data.return_value = _rows(as_of, count=5)
    client = Mock()
    request = HoldingReviewRequest(
        ticker="FLAT", quantity=1, buying_price="100", as_of=as_of
    )

    result = HoldingReviewEngine(repository, client).review(request)

    assert result.status == HoldingReviewStatus.BLOCKED
    assert "insufficient_stock_history_rows" in result.blocked_reasons
    client.chat.completions.create.assert_not_called()


def test_reduce_action_requires_stronger_review_and_rejection_becomes_review():
    as_of = date(2026, 8, 31)
    repository = _repository(as_of)
    client = Mock()
    client.chat.completions.create.side_effect = [
        _response(_decision("REDUCE")),
        _response(
            {
                "schema_version": "1.0",
                "approved": False,
                "rationale": "No replacement comparison supports reducing the holding yet.",
                "concerns": ["replacement_comparison_missing"],
            }
        ),
    ]
    request = HoldingReviewRequest(
        ticker="FLAT", quantity=10, buying_price="100", as_of=as_of
    )

    result = HoldingReviewEngine(repository, client).review(request)

    assert client.chat.completions.create.call_count == 2
    assert result.proposed_action == HoldingAction.REDUCE
    assert result.action_review is not None
    assert result.action_review.approved is False
    assert result.analysis is not None
    assert result.analysis.portfolio_action == HoldingAction.REVIEW


def test_ai_failure_is_failed_and_never_returns_heuristic_result():
    as_of = date(2026, 8, 31)
    repository = _repository(as_of)
    client = Mock()
    client.chat.completions.create.side_effect = RuntimeError("provider unavailable")

    result = HoldingReviewEngine(repository, client).review(
        HoldingReviewRequest(
            ticker="FLAT", quantity=10, buying_price="100", as_of=as_of
        )
    )

    assert result.status == HoldingReviewStatus.FAILED
    assert result.analysis is None
    assert "failed" in (result.error or "").lower()


def test_analyzer_handler_dispatches_internal_holding_review_mode():
    expected = {"statusCode": 200, "body": {"status": "COMPLETED"}}
    event = {"mode": "holding_review", "ticker": "FLAT"}

    with patch("src.analysis.ai_analyzer.run_holding_review", return_value=expected) as run:
        response = handler(event, None)

    assert response == expected
    run.assert_called_once_with(event)
