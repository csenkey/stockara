"""Tests for Phase 1 Pydantic models."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from src.models.schemas import (
    CandidateAnalysis,
    CandidateSignal,
    CompanySize,
    Recommendation,
    RiskLevel,
    SignalDirection,
    SignalSource,
    SignalType,
    Stock,
    StockData,
)


def test_stock_validates_sector_and_ticker():
    stock = Stock(
        ticker="aapl",
        company_name="Apple Inc.",
        sector="Technology",
        company_size=CompanySize.BLUE_CHIP,
    )
    assert stock.ticker == "AAPL"


def test_stock_rejects_invalid_sector():
    with pytest.raises(ValueError, match="Sector must be one of"):
        Stock(
            ticker="AAPL",
            company_name="Apple Inc.",
            sector="Invalid",
            company_size=CompanySize.BLUE_CHIP,
        )


def test_stock_data_accepts_ohlcv_record():
    record = StockData(
        ticker="MSFT",
        trading_date=date(2026, 6, 15),
        open_price=Decimal("420.10"),
        high_price=Decimal("425.20"),
        low_price=Decimal("419.00"),
        close_price=Decimal("424.55"),
        volume=1000,
    )
    assert record.ticker == "MSFT"


def test_candidate_signal_bounds_score():
    with pytest.raises(ValueError):
        CandidateSignal(
            ticker="NVDA",
            signal_type=SignalType.PRICE_MOVE,
            direction=SignalDirection.POSITIVE,
            score=101,
            title="Too high",
            summary="Invalid signal score",
            source=SignalSource(provider="test", observed_at=datetime.utcnow()),
        )


def test_candidate_analysis_models_shortlisted_ai_result():
    analysis = CandidateAnalysis(
        ticker="NVDA",
        analysis_date=date(2026, 6, 15),
        recommendation=Recommendation.BUY,
        risk_level=RiskLevel.MEDIUM,
        confidence_score=82,
        catalyst="Unusual volume",
        expected_timeframe="1-30 days",
        reasoning="Momentum is strong after a catalyst cluster.",
        invalidation_criteria="Volume fades and price loses support.",
        opportunity_score=77,
        negative_score=10,
    )
    assert analysis.recommendation == Recommendation.BUY
