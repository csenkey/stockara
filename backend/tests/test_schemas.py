"""Tests for Pydantic models and schemas."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from src.models.schemas import (
    AnalysisResult,
    CompanySize,
    NewsSummary,
    Portfolio,
    PortfolioHolding,
    Recommendation,
    RiskLevel,
    Stock,
    StockData,
    Suggestion,
    Timeframe,
    UserPreferences,
)


class TestStock:
    def test_valid_stock(self):
        stock = Stock(
            ticker="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            company_size=CompanySize.BLUE_CHIP,
        )
        assert stock.ticker == "AAPL"
        assert stock.company_size == CompanySize.BLUE_CHIP

    def test_ticker_uppercased(self):
        stock = Stock(
            ticker="aapl",
            company_name="Apple Inc.",
            sector="Technology",
            company_size=CompanySize.BLUE_CHIP,
        )
        assert stock.ticker == "AAPL"

    def test_invalid_sector(self):
        with pytest.raises(ValueError, match="Sector must be one of"):
            Stock(
                ticker="AAPL",
                company_name="Apple Inc.",
                sector="InvalidSector",
                company_size=CompanySize.BLUE_CHIP,
            )

    def test_ticker_too_long(self):
        with pytest.raises(ValueError, match="at most 10 characters"):
            Stock(
                ticker="TOOLONGTICKER",
                company_name="Test",
                sector="Technology",
                company_size=CompanySize.MID_CAP,
            )

    def test_empty_ticker(self):
        with pytest.raises(ValueError, match="must not be empty"):
            Stock(
                ticker="",
                company_name="Test",
                sector="Technology",
                company_size=CompanySize.MID_CAP,
            )


class TestStockData:
    def test_valid_stock_data(self):
        data = StockData(
            ticker="MSFT",
            trading_date=date(2025, 1, 15),
            open_price=Decimal("420.0000"),
            high_price=Decimal("425.5000"),
            low_price=Decimal("418.0000"),
            close_price=Decimal("423.7500"),
            volume=15000000,
        )
        assert data.ticker == "MSFT"
        assert data.volume == 15000000

    def test_negative_price_rejected(self):
        with pytest.raises(ValueError):
            StockData(
                ticker="MSFT",
                trading_date=date(2025, 1, 15),
                open_price=Decimal("-1.0000"),
                high_price=Decimal("425.5000"),
                low_price=Decimal("418.0000"),
                close_price=Decimal("423.7500"),
                volume=15000000,
            )

    def test_zero_price_rejected(self):
        with pytest.raises(ValueError):
            StockData(
                ticker="MSFT",
                trading_date=date(2025, 1, 15),
                open_price=Decimal("0"),
                high_price=Decimal("425.5000"),
                low_price=Decimal("418.0000"),
                close_price=Decimal("423.7500"),
                volume=15000000,
            )

    def test_negative_volume_rejected(self):
        with pytest.raises(ValueError):
            StockData(
                ticker="MSFT",
                trading_date=date(2025, 1, 15),
                open_price=Decimal("420.0000"),
                high_price=Decimal("425.5000"),
                low_price=Decimal("418.0000"),
                close_price=Decimal("423.7500"),
                volume=-1,
            )


class TestNewsSummary:
    def test_valid_news_summary(self):
        news = NewsSummary(
            title="Apple Reports Strong Q4 Earnings",
            source="Reuters",
            published_at=datetime(2025, 1, 15, 10, 30, 0),
            tickers=["AAPL"],
            summary="Apple reported record revenue driven by iPhone sales.",
            is_classified=True,
        )
        assert news.tickers == ["AAPL"]

    def test_summary_max_500_chars(self):
        with pytest.raises(ValueError):
            NewsSummary(
                title="Test",
                source="Reuters",
                published_at=datetime(2025, 1, 15),
                tickers=[],
                summary="x" * 501,
            )

    def test_tickers_uppercased(self):
        news = NewsSummary(
            title="Test Article",
            source="Bloomberg",
            published_at=datetime(2025, 1, 15),
            tickers=["aapl", "msft"],
            summary="Test summary.",
        )
        assert news.tickers == ["AAPL", "MSFT"]


class TestAnalysisResult:
    def test_valid_analysis(self):
        result = AnalysisResult(
            ticker="TSLA",
            analysis_date=date(2025, 1, 15),
            short_term_recommendation=Recommendation.BUY,
            long_term_recommendation=Recommendation.HOLD,
            risk_level=RiskLevel.HIGH,
            confidence_score=75,
            reasoning="Strong momentum but high volatility.",
        )
        assert result.confidence_score == 75

    def test_confidence_score_too_high(self):
        with pytest.raises(ValueError):
            AnalysisResult(
                ticker="TSLA",
                analysis_date=date(2025, 1, 15),
                short_term_recommendation=Recommendation.BUY,
                long_term_recommendation=Recommendation.HOLD,
                risk_level=RiskLevel.HIGH,
                confidence_score=101,
            )

    def test_confidence_score_negative(self):
        with pytest.raises(ValueError):
            AnalysisResult(
                ticker="TSLA",
                analysis_date=date(2025, 1, 15),
                short_term_recommendation=Recommendation.BUY,
                long_term_recommendation=Recommendation.HOLD,
                risk_level=RiskLevel.HIGH,
                confidence_score=-1,
            )


class TestPortfolioHolding:
    def test_valid_holding(self):
        holding = PortfolioHolding(
            ticker="AAPL",
            quantity=50,
            buying_price=Decimal("175.20"),
            added_date=date(2025, 3, 15),
        )
        assert holding.quantity == 50

    def test_zero_quantity_rejected(self):
        with pytest.raises(ValueError):
            PortfolioHolding(
                ticker="AAPL",
                quantity=0,
                buying_price=Decimal("175.20"),
            )

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValueError):
            PortfolioHolding(
                ticker="AAPL",
                quantity=-5,
                buying_price=Decimal("175.20"),
            )

    def test_zero_buying_price_rejected(self):
        with pytest.raises(ValueError):
            PortfolioHolding(
                ticker="AAPL",
                quantity=10,
                buying_price=Decimal("0"),
            )

    def test_negative_buying_price_rejected(self):
        with pytest.raises(ValueError):
            PortfolioHolding(
                ticker="AAPL",
                quantity=10,
                buying_price=Decimal("-100.00"),
            )


class TestPortfolio:
    def test_valid_portfolio(self):
        portfolio = Portfolio(
            holdings=[
                PortfolioHolding(ticker="AAPL", quantity=50, buying_price=Decimal("175.20")),
                PortfolioHolding(ticker="MSFT", quantity=30, buying_price=Decimal("420.00")),
            ]
        )
        assert len(portfolio.holdings) == 2

    def test_empty_portfolio(self):
        portfolio = Portfolio()
        assert portfolio.holdings == []


class TestUserPreferences:
    def test_valid_preferences(self):
        prefs = UserPreferences(
            preferred_sectors=["Technology", "Healthcare"],
            preferred_sizes=[CompanySize.BLUE_CHIP, CompanySize.MID_CAP],
            max_risk_level=RiskLevel.MEDIUM,
        )
        assert prefs.max_risk_level == RiskLevel.MEDIUM

    def test_defaults(self):
        prefs = UserPreferences()
        assert prefs.preferred_sectors == []
        assert prefs.preferred_sizes == []
        assert prefs.max_risk_level == RiskLevel.HIGH

    def test_invalid_sector_in_preferences(self):
        with pytest.raises(ValueError, match="Invalid sector"):
            UserPreferences(preferred_sectors=["NotASector"])


class TestSuggestion:
    def test_valid_buy_suggestion(self):
        suggestion = Suggestion(
            ticker="NVDA",
            recommendation=Recommendation.BUY,
            risk_level=RiskLevel.MEDIUM,
            timeframe=Timeframe.SHORT_TERM,
        )
        assert suggestion.recommendation == Recommendation.BUY

    def test_valid_sell_suggestion(self):
        suggestion = Suggestion(
            ticker="AAPL",
            recommendation=Recommendation.SELL,
            risk_level=RiskLevel.LOW,
            timeframe=Timeframe.BOTH,
        )
        assert suggestion.recommendation == Recommendation.SELL

    def test_hold_rejected(self):
        with pytest.raises(ValueError, match="BUY or SELL, not HOLD"):
            Suggestion(
                ticker="AAPL",
                recommendation=Recommendation.HOLD,
                risk_level=RiskLevel.LOW,
                timeframe=Timeframe.SHORT_TERM,
            )
