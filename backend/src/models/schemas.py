"""Phase 1 Pydantic models for Stockara top picks and risk alerts."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class CompanySize(str, Enum):
    BLUE_CHIP = "blue_chip"
    MID_CAP = "mid_cap"
    STARTUP = "startup"


class Recommendation(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AnalysisMethod(str, Enum):
    AI = "ai"
    FALLBACK_HEURISTIC = "fallback_heuristic"
    SUPPRESSED = "suppressed"


class SignalDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SignalType(str, Enum):
    PRICE_MOVE = "price_move"
    VOLUME_MOVE = "volume_move"
    TECHNICAL_TREND = "technical_trend"
    VOLUME_PERSISTENCE = "volume_persistence"
    NEWS = "news"
    EARNINGS = "earnings"
    DIVIDEND = "dividend"
    OPTIONS = "options"
    ANALYST = "analyst"
    INSIDER = "insider"
    INSTITUTIONAL = "institutional"
    SOCIAL_MOMENTUM = "social_momentum"
    SECTOR_RELATIVE = "sector_relative"


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


def validate_ticker(value: str) -> str:
    value = value.strip().upper()
    if not value:
        raise ValueError("Ticker must not be empty")
    if len(value) > 10:
        raise ValueError("Ticker must be at most 10 characters")
    if not all(c.isalnum() or c in (".", "-") for c in value):
        raise ValueError("Ticker must contain only alphanumeric characters, dots, or hyphens")
    return value


class Stock(BaseModel):
    ticker: str = Field(..., max_length=10)
    company_name: str = Field(..., min_length=1, max_length=255)
    sector: str
    industry: Optional[str] = Field(default=None, max_length=255)
    company_size: CompanySize
    source: str = "seed"
    metadata_source: Optional[str] = Field(default=None, max_length=100)
    metadata_source_url: Optional[str] = Field(default=None, max_length=500)
    metadata_as_of: Optional[date] = None
    business_description: Optional[str] = Field(default=None, max_length=2000)
    flagship_products: list[str] = Field(default_factory=list)
    revenue_segments: list[str] = Field(default_factory=list)
    primary_customers: list[str] = Field(default_factory=list)
    geographic_exposure: list[str] = Field(default_factory=list)
    competitive_position: Optional[str] = Field(default=None, max_length=1000)
    key_static_risks: list[str] = Field(default_factory=list)
    exchange: Optional[str] = Field(default=None, max_length=50)
    currency: Optional[str] = Field(default=None, max_length=10)
    country: Optional[str] = Field(default=None, max_length=100)
    website: Optional[str] = Field(default=None, max_length=500)
    founded_year: Optional[int] = Field(default=None, ge=1600, le=2200)
    headquarters: Optional[str] = Field(default=None, max_length=255)
    ipo_year: Optional[int] = Field(default=None, ge=1600, le=2200)
    market_cap: Optional[str] = Field(default=None, max_length=50)
    added_at: Optional[datetime] = None
    is_active: bool = True
    is_sell_alert_watch: bool = False

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, value: str) -> str:
        return validate_ticker(value)

    @field_validator("sector")
    @classmethod
    def validate_sector(cls, value: str) -> str:
        if value not in VALID_SECTORS:
            raise ValueError(f"Sector must be one of: {', '.join(VALID_SECTORS)}")
        return value


class StockData(BaseModel):
    ticker: str
    trading_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int = Field(..., ge=0)
    data_provider: Optional[str] = None
    provider_symbol: Optional[str] = None
    provider_endpoint: Optional[str] = None
    provider_priority: Optional[str] = None
    price_adjustment: Optional[str] = None
    adjusted_close_price: Optional[Decimal] = None
    has_adjusted_close: bool = False
    corporate_action_adjusted: Optional[bool] = None
    adjustment_context: Optional[str] = None
    split_dividend_adjustment: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    fetch_period: Optional[str] = None
    fetch_window_start: Optional[date] = None
    fetch_window_end: Optional[date] = None
    collected_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, value: str) -> str:
        return validate_ticker(value)


class NewsSummary(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    source: str = Field(..., min_length=1, max_length=100)
    published_at: datetime
    tickers: list[str] = Field(default_factory=list)
    summary: str = Field(..., min_length=1, max_length=500)
    sentiment: SignalDirection = SignalDirection.NEUTRAL
    is_classified: bool = True

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, value: list[str]) -> list[str]:
        return [validate_ticker(t) for t in value]


class EarningsEvent(BaseModel):
    ticker: str
    event_date: date
    company_name: Optional[str] = None
    eps_estimate: Optional[Decimal] = None
    reported_eps: Optional[Decimal] = None
    surprise_percent: Optional[Decimal] = None
    time_of_day: Optional[str] = None
    is_upcoming: bool
    price_before: Optional[Decimal] = None
    price_after: Optional[Decimal] = None
    post_earnings_price_move_percent: Optional[Decimal] = None
    provider: str = "yfinance"
    source_url: Optional[str] = None
    collected_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, value: str) -> str:
        return validate_ticker(value)


class DividendEvent(BaseModel):
    ticker: str
    ex_dividend_date: date
    company_name: Optional[str] = None
    pay_date: Optional[date] = None
    dividend_amount: Optional[Decimal] = None
    dividend_yield: Optional[Decimal] = None
    is_upcoming: bool
    price_before: Optional[Decimal] = None
    price_after: Optional[Decimal] = None
    post_ex_dividend_price_move_percent: Optional[Decimal] = None
    provider: str = "yfinance"
    source_url: Optional[str] = None
    collected_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, value: str) -> str:
        return validate_ticker(value)


class SignalSource(BaseModel):
    provider: str
    url: Optional[str] = None
    observed_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)


class CandidateSignal(BaseModel):
    ticker: str
    signal_type: SignalType
    direction: SignalDirection
    score: int = Field(..., ge=-100, le=100)
    title: str
    summary: str
    source: SignalSource

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, value: str) -> str:
        return validate_ticker(value)


class CandidateScore(BaseModel):
    ticker: str
    score_date: date
    opportunity_score: int
    negative_score: int
    signals: list[CandidateSignal] = Field(default_factory=list)
    created_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, value: str) -> str:
        return validate_ticker(value)


class CandidateAnalysis(BaseModel):
    ticker: str
    analysis_date: date
    analysis_method: AnalysisMethod = AnalysisMethod.AI
    publication_allowed: bool = True
    recommendation: Recommendation
    risk_level: RiskLevel
    confidence_score: int = Field(..., ge=0, le=100)
    catalyst: str
    expected_timeframe: str
    reasoning: str
    invalidation_criteria: str
    opportunity_score: int
    negative_score: int
    signals: list[CandidateSignal] = Field(default_factory=list)
    created_at: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def validate_ticker_field(cls, value: str) -> str:
        return validate_ticker(value)


class TopPick(BaseModel):
    rank: int
    ticker: str
    company_name: str
    sector: str
    analysis_method: AnalysisMethod = AnalysisMethod.AI
    recommendation: Recommendation
    risk_level: RiskLevel
    confidence_score: int
    catalyst: str
    expected_timeframe: str
    rationale: str
    invalidation_criteria: str
    supporting_evidence: list[str]
    source_traceability: list[SignalSource]


class SellAlert(BaseModel):
    rank: int
    ticker: str
    company_name: str
    sector: str
    analysis_method: AnalysisMethod = AnalysisMethod.AI
    severity: str
    risk_level: RiskLevel
    confidence_score: int
    negative_catalyst: str
    rationale: str
    supporting_evidence: list[str]
    source_traceability: list[SignalSource]


class ReviewRejection(BaseModel):
    ticker: str
    company_name: str
    sector: Optional[str] = None
    analysis_method: AnalysisMethod = AnalysisMethod.AI
    analysis_model: Optional[str] = None
    recommendation: Recommendation
    risk_level: RiskLevel
    confidence_score: int
    opportunity_score: int
    negative_score: int
    catalyst: str
    analyst_reasoning: str
    invalidation_criteria: str
    supporting_evidence: list[str] = Field(default_factory=list)
    ai_review: dict[str, Any]


class PublishedTopPicks(BaseModel):
    publication_date: date
    generated_at: datetime
    top_picks: list[TopPick]
    sell_alerts: list[SellAlert]
    review_rejections: list[ReviewRejection] = Field(default_factory=list)
    candidate_count: int
    analyzed_count: int
    data_warnings: list[str] = Field(default_factory=list)
