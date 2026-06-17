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


def test_stock_accepts_static_business_metadata():
    stock = Stock(
        ticker="AAPL",
        company_name="Apple Inc.",
        sector="Technology",
        industry="Consumer Electronics",
        company_size=CompanySize.BLUE_CHIP,
        metadata_source="company_profile",
        metadata_source_url="https://www.apple.com/investor-relations/",
        metadata_as_of=date(2026, 6, 17),
        business_description="Designs and sells consumer electronics and services.",
        flagship_products=["iPhone", "Mac", "Services"],
        revenue_segments=["Products", "Services"],
        primary_customers=["Consumers", "Businesses"],
        geographic_exposure=["Americas", "Europe", "Greater China"],
        competitive_position="Global premium consumer technology ecosystem.",
        key_static_risks=["Supply chain concentration", "Regulatory pressure"],
        exchange="NASDAQ",
        currency="USD",
        country="United States",
        website="https://www.apple.com",
        founded_year=1976,
        headquarters="Cupertino, California",
    )
    assert stock.industry == "Consumer Electronics"
    assert stock.flagship_products == ["iPhone", "Mac", "Services"]


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
        data_provider="yfinance",
        provider_priority="primary",
        price_adjustment="unadjusted",
        adjustment_context="raw_ohlcv_with_adjusted_close",
        split_dividend_adjustment="adjusted_close_available",
        exchange="NASDAQ",
        currency="USD",
        fetch_period="5y",
        fetch_window_start=date(2021, 6, 15),
        fetch_window_end=date(2026, 6, 15),
    )
    assert record.ticker == "MSFT"
    assert record.data_provider == "yfinance"
    assert record.fetch_window_start == date(2021, 6, 15)


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
