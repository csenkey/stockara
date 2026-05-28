"""FastAPI router for stock watchlist CRUD operations."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from psycopg2.extras import RealDictCursor

import structlog

from backend.src.db.connection import get_db_connection
from backend.src.models.schemas import CompanySize, Stock, VALID_SECTORS, validate_ticker

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


# --- Request/Response Models ---


class StockCreate(BaseModel):
    """Request body for adding a stock to the watchlist."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    company_name: str = Field(..., min_length=1, max_length=255, description="Company name")
    sector: str = Field(..., description="Company sector")
    company_size: CompanySize = Field(..., description="Company size classification")

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)

    @field_validator("sector")
    @classmethod
    def validate_sector(cls, v: str) -> str:
        if v not in VALID_SECTORS:
            raise ValueError(f"Sector must be one of: {', '.join(VALID_SECTORS)}")
        return v


class StockUpdate(BaseModel):
    """Request body for updating stock metadata."""

    company_name: Optional[str] = Field(None, min_length=1, max_length=255)
    sector: Optional[str] = Field(None)
    company_size: Optional[CompanySize] = Field(None)
    is_active: Optional[bool] = Field(None)

    @field_validator("sector")
    @classmethod
    def validate_sector(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_SECTORS:
            raise ValueError(f"Sector must be one of: {', '.join(VALID_SECTORS)}")
        return v


class StockResponse(BaseModel):
    """Response model for a stock."""

    ticker: str
    company_name: str
    sector: str
    company_size: str
    added_at: Optional[str] = None
    is_active: bool


class StockListResponse(BaseModel):
    """Response model for listing stocks."""

    stocks: list[StockResponse]
    total: int


# --- Endpoints ---


@router.get("", response_model=StockListResponse)
async def list_stocks(
    sector: Optional[str] = Query(None, description="Filter by sector"),
    company_size: Optional[CompanySize] = Query(None, description="Filter by company size"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
):
    """List monitored stocks with optional filters."""
    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = "SELECT ticker, company_name, sector, company_size, added_at, is_active FROM stocks WHERE 1=1"
            params: list = []

            if sector is not None:
                if sector not in VALID_SECTORS:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid sector. Must be one of: {', '.join(VALID_SECTORS)}",
                    )
                query += " AND sector = %s"
                params.append(sector)

            if company_size is not None:
                query += " AND company_size = %s"
                params.append(company_size.value)

            if is_active is not None:
                query += " AND is_active = %s"
                params.append(is_active)

            query += " ORDER BY ticker"

            cur.execute(query, params)
            rows = cur.fetchall()

            stocks = [
                StockResponse(
                    ticker=row["ticker"],
                    company_name=row["company_name"],
                    sector=row["sector"],
                    company_size=row["company_size"],
                    added_at=row["added_at"].isoformat() if row["added_at"] else None,
                    is_active=row["is_active"],
                )
                for row in rows
            ]

            return StockListResponse(stocks=stocks, total=len(stocks))


@router.post("", response_model=StockResponse, status_code=201)
async def add_stock(stock: StockCreate):
    """Add a new stock to the watchlist."""
    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check if ticker already exists
            cur.execute("SELECT ticker FROM stocks WHERE ticker = %s", (stock.ticker,))
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail=f"Stock with ticker '{stock.ticker}' already exists",
                )

            cur.execute(
                """
                INSERT INTO stocks (ticker, company_name, sector, company_size)
                VALUES (%s, %s, %s, %s)
                RETURNING ticker, company_name, sector, company_size, added_at, is_active
                """,
                (stock.ticker, stock.company_name, stock.sector, stock.company_size.value),
            )
            row = cur.fetchone()

            logger.info("Stock added to watchlist", ticker=stock.ticker, sector=stock.sector)

            return StockResponse(
                ticker=row["ticker"],
                company_name=row["company_name"],
                sector=row["sector"],
                company_size=row["company_size"],
                added_at=row["added_at"].isoformat() if row["added_at"] else None,
                is_active=row["is_active"],
            )


@router.delete("/{ticker}", status_code=200)
async def remove_stock(ticker: str):
    """Remove a stock from the watchlist."""
    ticker = ticker.strip().upper()

    async with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM stocks WHERE ticker = %s", (ticker,))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail=f"Stock with ticker '{ticker}' not found",
                )

            cur.execute("DELETE FROM stocks WHERE ticker = %s", (ticker,))
            logger.info("Stock removed from watchlist", ticker=ticker)

            return {"message": f"Stock '{ticker}' removed from watchlist"}


@router.put("/{ticker}", response_model=StockResponse)
async def update_stock(ticker: str, update: StockUpdate):
    """Update stock metadata."""
    ticker = ticker.strip().upper()

    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT ticker FROM stocks WHERE ticker = %s", (ticker,))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail=f"Stock with ticker '{ticker}' not found",
                )

            # Build dynamic update query
            updates = []
            params = []

            if update.company_name is not None:
                updates.append("company_name = %s")
                params.append(update.company_name)

            if update.sector is not None:
                updates.append("sector = %s")
                params.append(update.sector)

            if update.company_size is not None:
                updates.append("company_size = %s")
                params.append(update.company_size.value)

            if update.is_active is not None:
                updates.append("is_active = %s")
                params.append(update.is_active)

            if not updates:
                raise HTTPException(
                    status_code=400,
                    detail="No fields to update",
                )

            params.append(ticker)
            query = f"UPDATE stocks SET {', '.join(updates)} WHERE ticker = %s RETURNING ticker, company_name, sector, company_size, added_at, is_active"

            cur.execute(query, params)
            row = cur.fetchone()

            logger.info("Stock updated", ticker=ticker)

            return StockResponse(
                ticker=row["ticker"],
                company_name=row["company_name"],
                sector=row["sector"],
                company_size=row["company_size"],
                added_at=row["added_at"].isoformat() if row["added_at"] else None,
                is_active=row["is_active"],
            )
