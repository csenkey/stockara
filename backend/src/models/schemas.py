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


class SignalDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SignalType(str, Enum):
    PRICE_MOVE = "price_move"
    VOLUME_MOVE = "volume_move"
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
    company_size: CompanySize
    source: str = "seed"
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
    severity: str
    risk_level: RiskLevel
    confidence_score: int
    negative_catalyst: str
    rationale: str
    supporting_evidence: list[str]
    source_traceability: list[SignalSource]


class PublishedTopPicks(BaseModel):
    publication_date: date
    generated_at: datetime
    top_picks: list[TopPick]
    sell_alerts: list[SellAlert]
    candidate_count: int
    analyzed_count: int
    data_warnings: list[str] = Field(default_factory=list)
