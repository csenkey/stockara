"""FastAPI router for stock watchlist CRUD operations."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

import structlog

from backend.src.db.connection import store
from backend.src.models.schemas import CompanySize, VALID_SECTORS, validate_ticker

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
    if sector is not None and sector not in VALID_SECTORS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sector. Must be one of: {', '.join(VALID_SECTORS)}",
        )

    rows = store.list_stocks(
        sector=sector,
        company_size=company_size.value if company_size is not None else None,
        is_active=is_active,
    )
    stocks = [StockResponse(**row) for row in rows]
    return StockListResponse(stocks=stocks, total=len(stocks))


@router.post("", response_model=StockResponse, status_code=201)
async def add_stock(stock: StockCreate):
    """Add a new stock to the watchlist."""
    if store.get_stock(stock.ticker):
        raise HTTPException(
            status_code=409,
            detail=f"Stock with ticker '{stock.ticker}' already exists",
        )

    row = store.put_stock(
        {
            "ticker": stock.ticker,
            "company_name": stock.company_name,
            "sector": stock.sector,
            "company_size": stock.company_size.value,
        },
        create_only=True,
    )
    logger.info("Stock added to watchlist", ticker=stock.ticker, sector=stock.sector)
    return StockResponse(**row)


@router.delete("/{ticker}", status_code=200)
async def remove_stock(ticker: str):
    """Remove a stock from the watchlist."""
    ticker = ticker.strip().upper()

    if not store.get_stock(ticker):
        raise HTTPException(
            status_code=404,
            detail=f"Stock with ticker '{ticker}' not found",
        )

    store.delete_stock(ticker)
    logger.info("Stock removed from watchlist", ticker=ticker)
    return {"message": f"Stock '{ticker}' removed from watchlist"}


@router.put("/{ticker}", response_model=StockResponse)
async def update_stock(ticker: str, update: StockUpdate):
    """Update stock metadata."""
    ticker = ticker.strip().upper()

    updates = update.model_dump(exclude_unset=True)
    if "company_size" in updates and updates["company_size"] is not None:
        updates["company_size"] = updates["company_size"].value

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        row = store.update_stock(ticker, updates)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Stock with ticker '{ticker}' not found",
        )

    logger.info("Stock updated", ticker=ticker)
    return StockResponse(**row)
