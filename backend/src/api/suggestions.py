"""FastAPI router for personalized stock suggestions and analysis endpoints."""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field

import structlog

from backend.src.db.connection import store
from backend.src.models.schemas import (
    CompanySize,
    Portfolio,
    PortfolioHolding,
    Recommendation,
    RiskLevel,
    Timeframe,
    UserPreferences,
)
from backend.src.services.encryption_service import DecryptionError, EncryptionService
from backend.src.services.suggestion_engine import (
    SuggestionEngineError,
    SuggestionItem,
    generate_suggestions,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["suggestions"])


# --- Auth Dependency ---


async def get_current_user_id(x_user_id: Optional[str] = Header(None)) -> UUID:
    """Extract user_id from request header.

    Placeholder dependency that will be replaced with Cognito JWT validation.
    Reads user_id from the X-User-Id header.
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user identity")


# --- Response Models ---


class SuggestionResponse(BaseModel):
    """A single suggestion item in the response."""

    ticker: str
    recommendation: Recommendation
    risk_level: RiskLevel
    timeframe: Timeframe
    confidence_score: int
    reasoning: Optional[str] = None


class SuggestionsListResponse(BaseModel):
    """Full suggestions response with sell and buy lists."""

    sell_suggestions: list[SuggestionResponse]
    buy_suggestions: list[SuggestionResponse]
    analysis_date: str


class AnalysisResponse(BaseModel):
    """Response model for a stock's latest analysis."""

    ticker: str
    analysis_date: str
    short_term_recommendation: Recommendation
    long_term_recommendation: Recommendation
    risk_level: RiskLevel
    confidence_score: int = Field(ge=0, le=100)
    reasoning: Optional[str] = None
    created_at: Optional[str] = None


# --- Helpers ---


def _suggestion_item_to_response(item: SuggestionItem) -> SuggestionResponse:
    """Convert a SuggestionItem dataclass to a response model."""
    return SuggestionResponse(
        ticker=item.ticker,
        recommendation=item.recommendation,
        risk_level=item.risk_level,
        timeframe=item.timeframe,
        confidence_score=item.confidence_score,
        reasoning=item.reasoning,
    )


async def _get_user_portfolio(user_id: UUID) -> Portfolio:
    """Retrieve and decrypt the user's portfolio from the database."""
    row = store.get_portfolio(str(user_id))
    if not row:
        return Portfolio(holdings=[])

    try:
        encryption_service = EncryptionService()
        portfolio_data = encryption_service.decrypt_portfolio(row["encrypted_data"])
    except DecryptionError:
        logger.error("Failed to decrypt portfolio", user_id=str(user_id))
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve portfolio data",
        )

    holdings = [
        PortfolioHolding(
            ticker=h["ticker"],
            quantity=h["quantity"],
            buying_price=h["buying_price"],
            added_date=h.get("added_date"),
        )
        for h in portfolio_data.get("holdings", [])
    ]
    return Portfolio(holdings=holdings)


async def _get_user_preferences(user_id: UUID) -> UserPreferences:
    """Retrieve user preferences from the database."""
    row = store.get_preferences(str(user_id))
    if not row:
        return UserPreferences()

    return UserPreferences(
        preferred_sectors=row.get("preferred_sectors") or [],
        preferred_sizes=[CompanySize(s) for s in (row.get("preferred_sizes") or [])],
        max_risk_level=RiskLevel(row["max_risk_level"])
        if row.get("max_risk_level")
        else RiskLevel.HIGH,
    )


# --- Endpoints ---


@router.get("/api/suggestions", response_model=SuggestionsListResponse)
async def get_suggestions(
    user_id: UUID = Depends(get_current_user_id),
    sector: Optional[str] = Query(None, description="Filter by sector"),
    company_size: Optional[str] = Query(None, description="Filter by company size"),
    max_risk: Optional[str] = Query(None, description="Filter by max risk level"),
):
    """Get personalized BUY/SELL suggestions for the authenticated user.

    Fetches the user's portfolio and preferences, then generates suggestions
    using the SuggestionEngine. Optional query params override stored preferences.
    """
    # Get user portfolio and preferences
    portfolio = await _get_user_portfolio(user_id)
    preferences = await _get_user_preferences(user_id)

    # Override preferences with query params if provided
    if sector:
        preferences.preferred_sectors = [sector]
    if company_size:
        try:
            preferences.preferred_sizes = [CompanySize(company_size)]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid company_size: '{company_size}'. Must be one of: blue_chip, mid_cap, startup",
            )
    if max_risk:
        try:
            preferences.max_risk_level = RiskLevel(max_risk)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid max_risk: '{max_risk}'. Must be one of: LOW, MEDIUM, HIGH",
            )

    try:
        result = await generate_suggestions(portfolio=portfolio, preferences=preferences)
    except SuggestionEngineError as e:
        logger.error("Suggestion engine error", error=str(e), user_id=str(user_id))
        raise HTTPException(status_code=500, detail=str(e))

    return SuggestionsListResponse(
        sell_suggestions=[
            _suggestion_item_to_response(s) for s in result.sell_suggestions
        ],
        buy_suggestions=[
            _suggestion_item_to_response(s) for s in result.buy_suggestions
        ],
        analysis_date=result.analysis_date.isoformat(),
    )


@router.get("/api/stocks/{ticker}/analysis", response_model=AnalysisResponse)
async def get_stock_analysis(ticker: str):
    """Get the latest analysis result for a specific stock.

    This endpoint is public (no auth required).
    Returns 404 if no analysis exists for the given ticker.
    """
    ticker = ticker.strip().upper()

    row = store.latest_analysis_for_ticker(ticker)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis found for ticker '{ticker}'",
        )

    return AnalysisResponse(
        ticker=row["ticker"],
        analysis_date=row["analysis_date"].isoformat(),
        short_term_recommendation=Recommendation(row["short_term_recommendation"]),
        long_term_recommendation=Recommendation(row["long_term_recommendation"]),
        risk_level=RiskLevel(row["risk_level"]),
        confidence_score=row["confidence_score"],
        reasoning=row["reasoning"],
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
    )
