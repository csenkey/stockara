"""FastAPI router for portfolio management endpoints."""

import os
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field, field_validator
from psycopg2.extras import RealDictCursor

import structlog

from backend.src.db.connection import get_db_connection
from backend.src.models.schemas import Portfolio, PortfolioHolding, validate_ticker
from backend.src.services.encryption_service import (
    DecryptionError,
    EncryptionService,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# --- Auth Dependency ---


async def get_current_user_id(x_user_id: Optional[str] = Header(None)) -> UUID:
    """Extract user_id from request header.

    This is a placeholder dependency that will be replaced with Cognito JWT
    validation later. For now, it reads user_id from the X-User-Id header.
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user identity")


# --- Request/Response Models ---


class AddStockRequest(BaseModel):
    """Request body for adding a stock to the portfolio."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    quantity: int = Field(..., gt=0, description="Number of shares (positive integer)")
    buying_price: Decimal = Field(..., gt=0, description="Purchase price per share (positive)")
    added_date: Optional[date] = Field(default=None, description="Date the holding was added")

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)


class PortfolioResponse(BaseModel):
    """Response model for portfolio data."""

    holdings: list[dict]
    updated_at: Optional[str] = None


# --- Helpers ---


def _get_encryption_service() -> EncryptionService:
    """Create an EncryptionService instance."""
    return EncryptionService()


# --- Endpoints ---


@router.get("", response_model=PortfolioResponse)
async def get_portfolio(user_id: UUID = Depends(get_current_user_id)):
    """Get the decrypted portfolio for the authenticated user."""
    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT encrypted_data, updated_at FROM portfolios WHERE user_id = %s",
                (str(user_id),),
            )
            row = cur.fetchone()

            if not row:
                return PortfolioResponse(holdings=[], updated_at=None)

            try:
                encryption_service = _get_encryption_service()
                portfolio_data = encryption_service.decrypt_portfolio(row["encrypted_data"])
            except DecryptionError:
                logger.error("Failed to decrypt portfolio", user_id=str(user_id))
                raise HTTPException(
                    status_code=500,
                    detail="Failed to retrieve portfolio data",
                )

            return PortfolioResponse(
                holdings=portfolio_data.get("holdings", []),
                updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
            )


@router.put("/stocks", response_model=PortfolioResponse)
async def add_stock_to_portfolio(
    request: AddStockRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    """Add a stock to the authenticated user's portfolio.

    Validates that the ticker exists in the stocks watchlist and that
    quantity/buying_price are positive.
    """
    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Validate ticker exists in the stocks watchlist
            cur.execute(
                "SELECT ticker FROM stocks WHERE ticker = %s",
                (request.ticker,),
            )
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Ticker '{request.ticker}' does not exist in the watchlist",
                )

            # Get existing portfolio or start fresh
            cur.execute(
                "SELECT encrypted_data FROM portfolios WHERE user_id = %s",
                (str(user_id),),
            )
            row = cur.fetchone()

            encryption_service = _get_encryption_service()

            if row:
                try:
                    portfolio_data = encryption_service.decrypt_portfolio(row["encrypted_data"])
                except DecryptionError:
                    logger.error("Failed to decrypt portfolio for update", user_id=str(user_id))
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to retrieve portfolio data",
                    )
            else:
                portfolio_data = {"holdings": []}

            # Add the new holding
            new_holding = {
                "ticker": request.ticker,
                "quantity": request.quantity,
                "buying_price": float(request.buying_price),
                "added_date": request.added_date.isoformat() if request.added_date else date.today().isoformat(),
            }
            portfolio_data["holdings"].append(new_holding)

            # Encrypt and store
            encrypted_data = encryption_service.encrypt_portfolio(portfolio_data)

            cur.execute(
                """
                INSERT INTO portfolios (user_id, encrypted_data, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET encrypted_data = EXCLUDED.encrypted_data, updated_at = NOW()
                RETURNING updated_at
                """,
                (str(user_id), encrypted_data),
            )
            result = cur.fetchone()

            logger.info(
                "Stock added to portfolio",
                user_id=str(user_id),
                ticker=request.ticker,
            )

            return PortfolioResponse(
                holdings=portfolio_data["holdings"],
                updated_at=result["updated_at"].isoformat() if result["updated_at"] else None,
            )


@router.delete("/stocks/{ticker}")
async def remove_stock_from_portfolio(
    ticker: str,
    user_id: UUID = Depends(get_current_user_id),
):
    """Remove a stock from the authenticated user's portfolio."""
    ticker = validate_ticker(ticker)

    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get existing portfolio
            cur.execute(
                "SELECT encrypted_data FROM portfolios WHERE user_id = %s",
                (str(user_id),),
            )
            row = cur.fetchone()

            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Portfolio not found",
                )

            encryption_service = _get_encryption_service()

            try:
                portfolio_data = encryption_service.decrypt_portfolio(row["encrypted_data"])
            except DecryptionError:
                logger.error("Failed to decrypt portfolio for removal", user_id=str(user_id))
                raise HTTPException(
                    status_code=500,
                    detail="Failed to retrieve portfolio data",
                )

            # Find and remove the holding
            original_count = len(portfolio_data["holdings"])
            portfolio_data["holdings"] = [
                h for h in portfolio_data["holdings"] if h["ticker"] != ticker
            ]

            if len(portfolio_data["holdings"]) == original_count:
                raise HTTPException(
                    status_code=404,
                    detail=f"Stock '{ticker}' not found in portfolio",
                )

            # Encrypt and store updated portfolio
            encrypted_data = encryption_service.encrypt_portfolio(portfolio_data)

            cur.execute(
                """
                UPDATE portfolios
                SET encrypted_data = %s, updated_at = NOW()
                WHERE user_id = %s
                """,
                (encrypted_data, str(user_id)),
            )

            logger.info(
                "Stock removed from portfolio",
                user_id=str(user_id),
                ticker=ticker,
            )

            return {"message": f"Stock '{ticker}' removed from portfolio"}
