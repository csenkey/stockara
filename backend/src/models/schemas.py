"""Pydantic models and schemas for the Stock Monitoring and Analysis System."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# --- Enums ---


class CompanySize(str, Enum):
    """Company size classification."""

    BLUE_CHIP = "blue_chip"
    MID_CAP = "mid_cap"
    STARTUP = "startup"


class Recommendation(str, Enum):
    """Stock recommendation classification."""

    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


class RiskLevel(str, Enum):
    """Risk level classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Timeframe(str, Enum):
    """Recommendation timeframe."""

    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    BOTH = "both"


class PriceImpactDirection(str, Enum):
    """Observed direction of a price move after a business event."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


# --- Predefined Sectors ---

VALID_SECTORS = [
    "Technology",
    "Healthcare",
    "Finance",
    "Energy",
    "Consumer Discretionary",
    "Consumer Staples",
    "Industrials",
    "Materials",
    "Utilities",
    "Real Estate",
    "Communication Services",
    "Telecommunications",
]


# --- Validators ---


def validate_ticker(value: str) -> str:
    """Validate stock ticker format: 1-10 uppercase alphanumeric characters."""
    value = value.strip().upper()
    if not value:
        raise ValueError("Ticker must not be empty")
    if len(value) > 10:
        raise ValueError("Ticker must be at most 10 characters")
    if not all(c.isalnum() or c in (".", "-") for c in value):
        raise ValueError("Ticker must contain only alphanumeric characters, dots, or hyphens")
    return value


# --- Models ---


class Stock(BaseModel):
    """A monitored stock in the watchlist."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    company_name: str = Field(..., min_length=1, max_length=255, description="Company name")
    sector: str = Field(..., description="Company sector from predefined list")
    company_size: CompanySize = Field(..., description="Company size classification")
    added_at: Optional[datetime] = Field(default=None, description="When the stock was added")
    is_active: bool = Field(default=True, description="Whether the stock is actively monitored")

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


class StockProfile(BaseModel):
    """Rich business profile for a monitored stock."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    company_history: Optional[str] = Field(
        default=None, max_length=5000, description="Brief company history"
    )
    business_description: Optional[str] = Field(
        default=None, max_length=5000, description="Current business description"
    )
    leading_products: list[str] = Field(
        default_factory=list, description="Leading products or services"
    )
    business_stats: dict[str, Decimal | int | float | str] = Field(
        default_factory=dict, description="Financial and business statistics"
    )
    updated_at: Optional[datetime] = Field(default=None, description="Last profile update")

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)


class StockData(BaseModel):
    """Daily OHLCV data for a stock."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    trading_date: date = Field(..., description="The trading date")
    open_price: Decimal = Field(..., gt=0, decimal_places=4, description="Opening price")
    high_price: Decimal = Field(..., gt=0, decimal_places=4, description="Highest price")
    low_price: Decimal = Field(..., gt=0, decimal_places=4, description="Lowest price")
    close_price: Decimal = Field(..., gt=0, decimal_places=4, description="Closing price")
    volume: int = Field(..., ge=0, description="Trading volume")
    collected_at: Optional[datetime] = Field(default=None, description="When data was collected")

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)


class PriceImpact(BaseModel):
    """Price movement observed after a dividend, earnings call, or similar event."""

    window_days: int = Field(..., ge=0, le=365, description="Observation window in days")
    price_before: Optional[Decimal] = Field(default=None, gt=0, decimal_places=4)
    price_after: Optional[Decimal] = Field(default=None, gt=0, decimal_places=4)
    absolute_change: Optional[Decimal] = Field(default=None, decimal_places=4)
    percent_change: Optional[Decimal] = Field(default=None, decimal_places=4)
    benchmark_percent_change: Optional[Decimal] = Field(
        default=None,
        decimal_places=4,
        description="Sector or market benchmark movement over the same window",
    )
    abnormal_percent_change: Optional[Decimal] = Field(
        default=None,
        decimal_places=4,
        description="Ticker move minus benchmark move",
    )
    direction: PriceImpactDirection = Field(default=PriceImpactDirection.NEUTRAL)


class DividendEvent(BaseModel):
    """A dividend event and its observed stock-price impact."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    ex_dividend_date: date = Field(..., description="Ex-dividend date")
    dividend_value: Decimal = Field(..., gt=0, decimal_places=4)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    payment_date: Optional[date] = None
    price_impact: Optional[PriceImpact] = None
    collected_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class EarningsCallSummary(BaseModel):
    """Summarized earnings call and its observed stock-price impact."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    call_date: date = Field(..., description="Earnings call date")
    fiscal_period: str = Field(..., min_length=1, max_length=20)
    summary: str = Field(..., min_length=1, max_length=5000)
    key_topics: list[str] = Field(default_factory=list)
    sentiment: Optional[str] = Field(default=None, max_length=50)
    price_impact: Optional[PriceImpact] = None
    collected_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)


class NewsSummary(BaseModel):
    """A summarized news article related to stocks."""

    title: str = Field(..., min_length=1, max_length=500, description="Article title")
    source: str = Field(..., min_length=1, max_length=100, description="News source")
    published_at: datetime = Field(..., description="Publication date and time")
    tickers: list[str] = Field(default_factory=list, description="Related stock tickers")
    summary: str = Field(..., min_length=1, max_length=500, description="Condensed summary text")
    is_classified: bool = Field(default=True, description="Whether tickers were identified")

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, v: list[str]) -> list[str]:
        return [validate_ticker(t) for t in v]


class AnalysisResult(BaseModel):
    """AI-generated analysis result for a stock."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    analysis_date: date = Field(..., description="Date of analysis")
    short_term_recommendation: Recommendation = Field(
        ..., description="Short-term (1-30 days) recommendation"
    )
    long_term_recommendation: Recommendation = Field(
        ..., description="Long-term (30+ days) recommendation"
    )
    risk_level: RiskLevel = Field(..., description="Risk level classification")
    confidence_score: int = Field(
        ..., ge=0, le=100, description="Confidence score (0-100)"
    )
    reasoning: Optional[str] = Field(default=None, description="Analysis reasoning")
    created_at: Optional[datetime] = Field(default=None, description="When analysis was created")

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)


class Sector(BaseModel):
    """A market sector tracked as its own analytical entity."""

    name: str = Field(..., description="Sector name from predefined list")
    description: Optional[str] = Field(default=None, max_length=2000)
    benchmark_symbol: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Optional ETF or benchmark used for sector trend comparisons",
    )
    updated_at: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if v not in VALID_SECTORS:
            raise ValueError(f"Sector must be one of: {', '.join(VALID_SECTORS)}")
        return v


class SectorTrend(BaseModel):
    """Daily or periodic sector trend data used for correlation calculations."""

    sector: str = Field(..., description="Sector name from predefined list")
    trend_date: date
    benchmark_symbol: Optional[str] = Field(default=None, max_length=20)
    benchmark_close: Optional[Decimal] = Field(default=None, gt=0, decimal_places=4)
    percent_change: Optional[Decimal] = Field(default=None, decimal_places=4)
    trend_score: Optional[Decimal] = Field(
        default=None,
        ge=-100,
        le=100,
        decimal_places=4,
        description="Normalized sector trend score",
    )
    collected_at: Optional[datetime] = None

    @field_validator("sector")
    @classmethod
    def validate_sector(cls, v: str) -> str:
        if v not in VALID_SECTORS:
            raise ValueError(f"Sector must be one of: {', '.join(VALID_SECTORS)}")
        return v


class SectorTickerCorrelation(BaseModel):
    """Correlation between a sector trend and one ticker over a date window."""

    sector: str = Field(..., description="Sector name from predefined list")
    ticker: str = Field(..., max_length=10)
    calculation_date: date
    window_days: int = Field(..., gt=0, le=3650)
    correlation: Decimal = Field(..., ge=-1, le=1, decimal_places=6)
    sample_size: int = Field(..., gt=1)
    method: str = Field(default="pearson", min_length=1, max_length=50)

    @field_validator("sector")
    @classmethod
    def validate_sector(cls, v: str) -> str:
        if v not in VALID_SECTORS:
            raise ValueError(f"Sector must be one of: {', '.join(VALID_SECTORS)}")
        return v

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)


class PortfolioHolding(BaseModel):
    """A single stock holding in a user's portfolio."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    quantity: int = Field(..., gt=0, description="Number of shares held (must be positive)")
    buying_price: Decimal = Field(
        ..., gt=0, description="Purchase price per share (must be positive)"
    )
    added_date: Optional[date] = Field(default=None, description="Date the holding was added")

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)


class Portfolio(BaseModel):
    """A user's complete portfolio."""

    holdings: list[PortfolioHolding] = Field(
        default_factory=list, description="List of stock holdings"
    )


class UserPreferences(BaseModel):
    """User preferences for filtering suggestions."""

    preferred_sectors: list[str] = Field(
        default_factory=list, description="Preferred sectors for suggestions"
    )
    preferred_sizes: list[CompanySize] = Field(
        default_factory=list, description="Preferred company sizes"
    )
    max_risk_level: RiskLevel = Field(
        default=RiskLevel.HIGH, description="Maximum acceptable risk level"
    )

    @field_validator("preferred_sectors")
    @classmethod
    def validate_preferred_sectors(cls, v: list[str]) -> list[str]:
        for sector in v:
            if sector not in VALID_SECTORS:
                raise ValueError(f"Invalid sector '{sector}'. Must be one of: {', '.join(VALID_SECTORS)}")
        return v


class Suggestion(BaseModel):
    """A personalized stock suggestion for a user."""

    ticker: str = Field(..., max_length=10, description="Stock ticker symbol")
    recommendation: Recommendation = Field(
        ..., description="Recommendation direction (BUY or SELL)"
    )
    risk_level: RiskLevel = Field(..., description="Associated risk level")
    timeframe: Timeframe = Field(..., description="Recommendation timeframe")

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)

    @field_validator("recommendation")
    @classmethod
    def validate_recommendation_direction(cls, v: Recommendation) -> Recommendation:
        if v == Recommendation.HOLD:
            raise ValueError("Suggestions must be BUY or SELL, not HOLD")
        return v


class SuggestionHistory(BaseModel):
    """Suggestions generated for one customer on one analysis date."""

    user_id: str = Field(..., min_length=1)
    suggestion_date: date
    analysis_date: date
    sell_suggestions: list[Suggestion] = Field(default_factory=list)
    buy_suggestions: list[Suggestion] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class TopPick(BaseModel):
    """Public daily top-pick content generated from analysis."""

    pick_date: date
    ticker: str = Field(..., max_length=10)
    company_name: Optional[str] = Field(default=None, max_length=255)
    reasoning: str = Field(..., min_length=1, max_length=5000)
    analysis_date: Optional[date] = None
    generated_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, v: str) -> str:
        return validate_ticker(v)
