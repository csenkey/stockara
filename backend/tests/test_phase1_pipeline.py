"""Tests for Phase 1 scoring and publication ranking."""

from datetime import date
from unittest.mock import patch

from src.analysis.phase1_pipeline import build_publication_payload, select_shortlist


def test_select_shortlist_prioritizes_high_opportunity_and_sell_watch():
    scores = [
        {"ticker": "AAPL", "opportunity_score": 30, "negative_score": 10, "signals": []},
        {"ticker": "TSLA", "opportunity_score": 10, "negative_score": 45, "signals": []},
        {"ticker": "NVDA", "opportunity_score": 80, "negative_score": 0, "signals": []},
    ]

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=["TSLA"]):
        shortlist = select_shortlist(scores)

    assert [row["ticker"] for row in shortlist[:2]] == ["TSLA", "NVDA"]


def test_build_publication_payload_splits_top_picks_and_sell_alerts():
    stocks = [
        {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"},
        {"ticker": "TSLA", "company_name": "Tesla", "sector": "Consumer Discretionary"},
    ]
    scores = [
        {"ticker": "NVDA", "opportunity_score": 80, "negative_score": 5, "signals": []},
        {"ticker": "TSLA", "opportunity_score": 5, "negative_score": 90, "signals": []},
    ]
    analyses = [
        {
            "ticker": "NVDA",
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 84,
            "catalyst": "Unusual volume",
            "expected_timeframe": "1-30 days",
            "reasoning": "Momentum cluster is positive.",
            "invalidation_criteria": "Momentum fades.",
            "opportunity_score": 80,
            "negative_score": 5,
            "signals": [],
        },
        {
            "ticker": "TSLA",
            "recommendation": "SELL",
            "risk_level": "HIGH",
            "confidence_score": 78,
            "catalyst": "Negative news",
            "expected_timeframe": "1-7 days",
            "reasoning": "Negative catalyst cluster is severe.",
            "invalidation_criteria": "News reverses.",
            "opportunity_score": 5,
            "negative_score": 90,
            "signals": [],
        },
    ]

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=["TSLA"]):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 15))

    assert payload["top_picks"][0]["ticker"] == "NVDA"
    assert payload["sell_alerts"][0]["ticker"] == "TSLA"
