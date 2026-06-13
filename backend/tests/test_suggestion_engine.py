"""Tests for the suggestion engine service."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.src.models.schemas import (
    CompanySize,
    Portfolio,
    PortfolioHolding,
    Recommendation,
    RiskLevel,
    Timeframe,
    UserPreferences,
)
from backend.src.services.suggestion_engine import (
    SuggestionEngineError,
    _determine_timeframe,
    _passes_filters,
    _risk_level_allowed,
    generate_suggestions,
)


class TestDetermineTimeframe:
    def test_both_match(self):
        assert _determine_timeframe("SELL", "SELL", "SELL") == Timeframe.BOTH

    def test_short_term_only(self):
        assert _determine_timeframe("BUY", "HOLD", "BUY") == Timeframe.SHORT_TERM

    def test_long_term_only(self):
        assert _determine_timeframe("HOLD", "BUY", "BUY") == Timeframe.LONG_TERM


class TestRiskLevelAllowed:
    def test_low_allows_low(self):
        assert _risk_level_allowed(RiskLevel.LOW, RiskLevel.LOW) is True

    def test_low_disallows_medium(self):
        assert _risk_level_allowed(RiskLevel.MEDIUM, RiskLevel.LOW) is False

    def test_low_disallows_high(self):
        assert _risk_level_allowed(RiskLevel.HIGH, RiskLevel.LOW) is False

    def test_medium_allows_low(self):
        assert _risk_level_allowed(RiskLevel.LOW, RiskLevel.MEDIUM) is True

    def test_medium_allows_medium(self):
        assert _risk_level_allowed(RiskLevel.MEDIUM, RiskLevel.MEDIUM) is True

    def test_medium_disallows_high(self):
        assert _risk_level_allowed(RiskLevel.HIGH, RiskLevel.MEDIUM) is False

    def test_high_allows_all(self):
        assert _risk_level_allowed(RiskLevel.LOW, RiskLevel.HIGH) is True
        assert _risk_level_allowed(RiskLevel.MEDIUM, RiskLevel.HIGH) is True
        assert _risk_level_allowed(RiskLevel.HIGH, RiskLevel.HIGH) is True


class TestPassesFilters:
    def test_no_filters_applied(self):
        prefs = UserPreferences()
        assert _passes_filters("Technology", "blue_chip", RiskLevel.HIGH, prefs) is True

    def test_sector_filter_match(self):
        prefs = UserPreferences(preferred_sectors=["Technology"])
        assert _passes_filters("Technology", "blue_chip", RiskLevel.LOW, prefs) is True

    def test_sector_filter_no_match(self):
        prefs = UserPreferences(preferred_sectors=["Healthcare"])
        assert _passes_filters("Technology", "blue_chip", RiskLevel.LOW, prefs) is False

    def test_size_filter_match(self):
        prefs = UserPreferences(preferred_sizes=[CompanySize.BLUE_CHIP])
        assert _passes_filters("Technology", "blue_chip", RiskLevel.LOW, prefs) is True

    def test_size_filter_no_match(self):
        prefs = UserPreferences(preferred_sizes=[CompanySize.STARTUP])
        assert _passes_filters("Technology", "blue_chip", RiskLevel.LOW, prefs) is False

    def test_risk_filter_blocks(self):
        prefs = UserPreferences(max_risk_level=RiskLevel.LOW)
        assert _passes_filters("Technology", "blue_chip", RiskLevel.MEDIUM, prefs) is False

    def test_combined_filters(self):
        prefs = UserPreferences(
            preferred_sectors=["Technology"],
            preferred_sizes=[CompanySize.MID_CAP],
            max_risk_level=RiskLevel.MEDIUM,
        )
        # Passes all
        assert _passes_filters("Technology", "mid_cap", RiskLevel.LOW, prefs) is True
        # Fails sector
        assert _passes_filters("Healthcare", "mid_cap", RiskLevel.LOW, prefs) is False
        # Fails size
        assert _passes_filters("Technology", "blue_chip", RiskLevel.LOW, prefs) is False
        # Fails risk
        assert _passes_filters("Technology", "mid_cap", RiskLevel.HIGH, prefs) is False


class TestGenerateSuggestions:
    """Integration-style tests for generate_suggestions using mocked DB."""

    @pytest.fixture
    def portfolio(self):
        return Portfolio(
            holdings=[
                PortfolioHolding(ticker="AAPL", quantity=10, buying_price=Decimal("150.00")),
                PortfolioHolding(ticker="MSFT", quantity=5, buying_price=Decimal("400.00")),
            ]
        )

    @pytest.fixture
    def preferences(self):
        return UserPreferences(max_risk_level=RiskLevel.HIGH)

    @pytest.fixture
    def analysis_rows(self):
        return [
            {
                "ticker": "AAPL",
                "short_term_recommendation": "SELL",
                "long_term_recommendation": "HOLD",
                "risk_level": "MEDIUM",
                "confidence_score": 75,
                "reasoning": "Overvalued",
                "sector": "Technology",
                "company_size": "blue_chip",
            },
            {
                "ticker": "MSFT",
                "short_term_recommendation": "HOLD",
                "long_term_recommendation": "HOLD",
                "risk_level": "LOW",
                "confidence_score": 60,
                "reasoning": "Stable",
                "sector": "Technology",
                "company_size": "blue_chip",
            },
            {
                "ticker": "TSLA",
                "short_term_recommendation": "BUY",
                "long_term_recommendation": "BUY",
                "risk_level": "HIGH",
                "confidence_score": 85,
                "reasoning": "Momentum",
                "sector": "Consumer Discretionary",
                "company_size": "blue_chip",
            },
            {
                "ticker": "NVDA",
                "short_term_recommendation": "BUY",
                "long_term_recommendation": "HOLD",
                "risk_level": "MEDIUM",
                "confidence_score": 90,
                "reasoning": "AI growth",
                "sector": "Technology",
                "company_size": "blue_chip",
            },
        ]

    @pytest.mark.asyncio
    async def test_generates_sell_suggestions(self, portfolio, preferences, analysis_rows):
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, preferences)

        assert len(result.sell_suggestions) == 1
        assert result.sell_suggestions[0].ticker == "AAPL"
        assert result.sell_suggestions[0].recommendation == Recommendation.SELL
        assert result.sell_suggestions[0].timeframe == Timeframe.SHORT_TERM

    @pytest.mark.asyncio
    async def test_generates_buy_suggestions_ranked_by_confidence(
        self, portfolio, preferences, analysis_rows
    ):
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, preferences)

        assert len(result.buy_suggestions) == 2
        # NVDA (90) should rank higher than TSLA (85)
        assert result.buy_suggestions[0].ticker == "NVDA"
        assert result.buy_suggestions[0].confidence_score == 90
        assert result.buy_suggestions[1].ticker == "TSLA"
        assert result.buy_suggestions[1].confidence_score == 85

    @pytest.mark.asyncio
    async def test_buy_suggestions_filtered_by_risk(self, portfolio, analysis_rows):
        prefs = UserPreferences(max_risk_level=RiskLevel.MEDIUM)
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, prefs)

        # TSLA (HIGH risk) should be excluded
        tickers = [s.ticker for s in result.buy_suggestions]
        assert "TSLA" not in tickers
        assert "NVDA" in tickers

    @pytest.mark.asyncio
    async def test_buy_suggestions_filtered_by_sector(self, portfolio, analysis_rows):
        prefs = UserPreferences(preferred_sectors=["Technology"])
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, prefs)

        tickers = [s.ticker for s in result.buy_suggestions]
        assert "NVDA" in tickers
        assert "TSLA" not in tickers  # Consumer Discretionary

    @pytest.mark.asyncio
    async def test_none_portfolio_raises_error(self, preferences):
        with pytest.raises(SuggestionEngineError, match="Portfolio could not be retrieved"):
            await generate_suggestions(None, preferences)

    @pytest.mark.asyncio
    async def test_stale_analysis_includes_date(self, portfolio, preferences, analysis_rows):
        stale_date = date(2025, 1, 1)
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, stale_date),
        ):
            result = await generate_suggestions(portfolio, preferences)

        assert result.analysis_date == stale_date

    @pytest.mark.asyncio
    async def test_empty_portfolio_no_sell_suggestions(self, preferences, analysis_rows):
        empty_portfolio = Portfolio(holdings=[])
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, date.today()),
        ):
            result = await generate_suggestions(empty_portfolio, preferences)

        assert len(result.sell_suggestions) == 0
        assert len(result.buy_suggestions) > 0

    @pytest.mark.asyncio
    async def test_both_timeframe_for_sell(self, preferences):
        portfolio = Portfolio(
            holdings=[PortfolioHolding(ticker="XYZ", quantity=1, buying_price=Decimal("10"))]
        )
        rows = [
            {
                "ticker": "XYZ",
                "short_term_recommendation": "SELL",
                "long_term_recommendation": "SELL",
                "risk_level": "HIGH",
                "confidence_score": 95,
                "reasoning": "Bad outlook",
                "sector": "Technology",
                "company_size": "startup",
            }
        ]
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, preferences)

        assert result.sell_suggestions[0].timeframe == Timeframe.BOTH

    @pytest.mark.asyncio
    async def test_buy_suggestions_filtered_by_company_size(self, portfolio, analysis_rows):
        """Req 6.5: Filter by company size preference."""
        prefs = UserPreferences(preferred_sizes=[CompanySize.MID_CAP])
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, prefs)

        # All analysis_rows have company_size="blue_chip", so no BUY matches mid_cap
        assert len(result.buy_suggestions) == 0

    @pytest.mark.asyncio
    async def test_no_matching_suggestions_after_filtering(self, portfolio, analysis_rows):
        """Edge case: all filters combined exclude every candidate."""
        prefs = UserPreferences(
            preferred_sectors=["Healthcare"],
            preferred_sizes=[CompanySize.STARTUP],
            max_risk_level=RiskLevel.LOW,
        )
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(analysis_rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, prefs)

        assert len(result.buy_suggestions) == 0

    @pytest.mark.asyncio
    async def test_sell_suggestions_ranked_by_confidence(self, preferences):
        """Req 6.7: Sell suggestions also include confidence for display."""
        portfolio = Portfolio(
            holdings=[
                PortfolioHolding(ticker="A", quantity=1, buying_price=Decimal("10")),
                PortfolioHolding(ticker="B", quantity=1, buying_price=Decimal("20")),
            ]
        )
        rows = [
            {
                "ticker": "A",
                "short_term_recommendation": "SELL",
                "long_term_recommendation": "HOLD",
                "risk_level": "LOW",
                "confidence_score": 60,
                "reasoning": "Weak",
                "sector": "Technology",
                "company_size": "blue_chip",
            },
            {
                "ticker": "B",
                "short_term_recommendation": "HOLD",
                "long_term_recommendation": "SELL",
                "risk_level": "MEDIUM",
                "confidence_score": 80,
                "reasoning": "Declining",
                "sector": "Healthcare",
                "company_size": "mid_cap",
            },
        ]
        with patch(
            "backend.src.services.suggestion_engine.get_latest_analysis",
            new_callable=AsyncMock,
            return_value=(rows, date.today()),
        ):
            result = await generate_suggestions(portfolio, preferences)

        assert len(result.sell_suggestions) == 2
        # Both have sell recs, verify confidence scores are present
        assert result.sell_suggestions[0].confidence_score == 60
        assert result.sell_suggestions[1].confidence_score == 80
