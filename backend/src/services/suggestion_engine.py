"""
Suggestion Engine service for generating personalized stock recommendations.

Compares a user's portfolio against the latest AI analysis to produce:
- SELL suggestions: stocks in the portfolio with a SELL recommendation
- BUY suggestions: stocks not in the portfolio with a BUY recommendation,
  filtered by user preferences and ranked by confidence score descending.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from psycopg2.extras import RealDictCursor
import structlog

from backend.src.db.connection import get_db_connection
from backend.src.models.schemas import (
    Portfolio,
    Recommendation,
    RiskLevel,
    Timeframe,
    UserPreferences,
)

logger = structlog.get_logger(__name__)


# Risk level hierarchy for filtering: LOW < MEDIUM < HIGH
_RISK_LEVEL_ORDER = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
}


class SuggestionEngineError(Exception):
    """Raised when the suggestion engine cannot generate suggestions."""
    pass


@dataclass
class SuggestionItem:
    """A single stock suggestion."""

    ticker: str
    recommendation: Recommendation
    risk_level: RiskLevel
    timeframe: Timeframe
    confidence_score: int
    reasoning: Optional[str] = None


@dataclass
class SuggestionsResult:
    """The complete suggestion response for a user."""

    sell_suggestions: list[SuggestionItem]
    buy_suggestions: list[SuggestionItem]
    analysis_date: date


def _determine_timeframe(short_term: str, long_term: str, target: str) -> Timeframe:
    """Determine if the recommendation applies to short-term, long-term, or both."""
    short_match = short_term == target
    long_match = long_term == target
    if short_match and long_match:
        return Timeframe.BOTH
    elif short_match:
        return Timeframe.SHORT_TERM
    else:
        return Timeframe.LONG_TERM


def _risk_level_allowed(stock_risk: RiskLevel, max_risk: RiskLevel) -> bool:
    """Check if a stock's risk level is at or below the user's max risk preference."""
    return _RISK_LEVEL_ORDER[stock_risk] <= _RISK_LEVEL_ORDER[max_risk]


async def get_latest_analysis() -> tuple[list[dict], date]:
    """
    Fetch the latest analysis results from the database.

    Returns a tuple of (analysis_rows, analysis_date).
    If no analysis exists for today, uses the most recent available date.

    Raises:
        SuggestionEngineError: If no analysis data exists at all.
    """
    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find the most recent analysis date
            cur.execute(
                "SELECT MAX(analysis_date) as latest_date FROM analysis_results"
            )
            row = cur.fetchone()
            if row is None or row["latest_date"] is None:
                raise SuggestionEngineError("No analysis data available")

            latest_date = row["latest_date"]

            # Fetch all analysis for that date, joined with stock metadata
            cur.execute(
                """
                SELECT
                    ar.ticker,
                    ar.short_term_recommendation,
                    ar.long_term_recommendation,
                    ar.risk_level,
                    ar.confidence_score,
                    ar.reasoning,
                    s.sector,
                    s.company_size
                FROM analysis_results ar
                JOIN stocks s ON ar.ticker = s.ticker
                WHERE ar.analysis_date = %s
                  AND s.is_active = TRUE
                """,
                (latest_date,),
            )
            rows = cur.fetchall()

    return rows, latest_date


async def generate_suggestions(
    portfolio: Portfolio,
    preferences: UserPreferences,
) -> SuggestionsResult:
    """
    Generate personalized BUY and SELL suggestions for a user.

    Args:
        portfolio: The user's portfolio (must not be None).
        preferences: The user's filter preferences.

    Returns:
        SuggestionsResult with sell and buy suggestions.

    Raises:
        SuggestionEngineError: If portfolio is None or analysis data is unavailable.
    """
    if portfolio is None:
        raise SuggestionEngineError(
            "Portfolio could not be retrieved. Cannot generate suggestions."
        )

    # Get the set of tickers the user holds
    held_tickers = {holding.ticker for holding in portfolio.holdings}

    # Fetch latest analysis
    analysis_rows, analysis_date = await get_latest_analysis()

    today = date.today()
    if analysis_date != today:
        logger.info(
            "Using stale analysis data",
            analysis_date=str(analysis_date),
            today=str(today),
        )

    sell_suggestions: list[SuggestionItem] = []
    buy_suggestions: list[SuggestionItem] = []

    for row in analysis_rows:
        ticker = row["ticker"]
        short_rec = row["short_term_recommendation"]
        long_rec = row["long_term_recommendation"]
        risk_level = RiskLevel(row["risk_level"])
        confidence = row["confidence_score"]
        reasoning = row.get("reasoning")
        sector = row["sector"]
        company_size = row["company_size"]

        # SELL suggestions: stocks in portfolio with SELL recommendation
        if ticker in held_tickers:
            if short_rec == "SELL" or long_rec == "SELL":
                timeframe = _determine_timeframe(short_rec, long_rec, "SELL")
                sell_suggestions.append(
                    SuggestionItem(
                        ticker=ticker,
                        recommendation=Recommendation.SELL,
                        risk_level=risk_level,
                        timeframe=timeframe,
                        confidence_score=confidence,
                        reasoning=reasoning,
                    )
                )

        # BUY suggestions: stocks NOT in portfolio with BUY recommendation
        else:
            if short_rec == "BUY" or long_rec == "BUY":
                # Apply user preference filters
                if not _passes_filters(
                    sector=sector,
                    company_size=company_size,
                    risk_level=risk_level,
                    preferences=preferences,
                ):
                    continue

                timeframe = _determine_timeframe(short_rec, long_rec, "BUY")
                buy_suggestions.append(
                    SuggestionItem(
                        ticker=ticker,
                        recommendation=Recommendation.BUY,
                        risk_level=risk_level,
                        timeframe=timeframe,
                        confidence_score=confidence,
                        reasoning=reasoning,
                    )
                )

    # Rank BUY suggestions by confidence_score descending
    buy_suggestions.sort(key=lambda s: s.confidence_score, reverse=True)

    logger.info(
        "Suggestions generated",
        sell_count=len(sell_suggestions),
        buy_count=len(buy_suggestions),
        analysis_date=str(analysis_date),
    )

    return SuggestionsResult(
        sell_suggestions=sell_suggestions,
        buy_suggestions=buy_suggestions,
        analysis_date=analysis_date,
    )


def _passes_filters(
    sector: str,
    company_size: str,
    risk_level: RiskLevel,
    preferences: UserPreferences,
) -> bool:
    """
    Check if a stock passes all user preference filters.

    Filters are applied only when the user has specified preferences.
    An empty preference list means no filter is applied for that dimension.
    """
    # Sector filter
    if preferences.preferred_sectors and sector not in preferences.preferred_sectors:
        return False

    # Company size filter
    if preferences.preferred_sizes:
        if company_size not in [s.value for s in preferences.preferred_sizes]:
            return False

    # Risk level filter: allow stocks at or below max_risk_level
    if not _risk_level_allowed(risk_level, preferences.max_risk_level):
        return False

    return True
