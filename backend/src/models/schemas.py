"""Phase 1 Pydantic models for Stockara top picks and risk alerts."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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


class CollectionTaskType(str, Enum):
    PRICE = "price"
    NEWS = "news"
    EARNINGS = "earnings"
    DIVIDEND = "dividend"


class CollectionTaskStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    SKIPPED = "skipped"


class CollectionTickerHealth(str, Enum):
    HEALTHY = "healthy"
    TRANSIENT_FAILURE = "transient_failure"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNSUPPORTED = "provider_unsupported"
    SYMBOL_MAPPING_NEEDED = "symbol_mapping_needed"
    INACTIVE_OR_DELISTED = "inactive_or_delisted"


class RepairMode(str, Enum):
    SYNC_STATIC_METADATA = "sync_static_metadata"
    REPAIR_PRICE_GAPS = "repair_price_gaps"
    REPAIR_HISTORY = "repair_history"
    REPAIR_NEWS = "repair_news"
    REPAIR_CALENDARS = "repair_calendars"
    REPAIR_EVIDENCE = "repair_evidence"
    RETRY_AI_ANALYSIS = "retry_ai_analysis"
    RETRY_AI_REVIEW = "retry_ai_review"
    REPAIR_REVIEW_EVIDENCE = "repair_review_evidence"


class ReviewEvidenceGap(BaseModel):
    gap_type: str = Field(..., min_length=1, max_length=100)
    classification: Literal[
        "collectable",
        "feature_missing",
        "analysis_context_missing",
        "provider_failure",
        "not_collectable",
    ]
    description: str = Field(..., min_length=1, max_length=500)
    source_candidates: list[str] = Field(default_factory=list, max_length=5)


class AIReviewDecision(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    approved: bool
    rationale: str = Field(..., min_length=1, max_length=750)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    confidence_adjustment: int = Field(default=0, ge=-20, le=10)
    rejection_category: str = Field(default="", max_length=120)
    what_would_make_approvable: str = Field(default="", max_length=500)
    evidence_gaps: list[ReviewEvidenceGap] = Field(default_factory=list, max_length=8)

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("AI review rationale must not be empty")
        return value

    @model_validator(mode="after")
    def validate_rejection_explanation(self) -> "AIReviewDecision":
        if self.approved:
            return self
        if not self.rejection_category.strip():
            raise ValueError("Rejected reviews require rejection_category")
        if not self.what_would_make_approvable.strip():
            raise ValueError("Rejected reviews require what_would_make_approvable")
        return self


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


def collection_manifest_s3_key(manifest_date: date) -> str:
    """Return the canonical S3 key for a daily collection manifest."""
    return f"collection_manifest/{manifest_date.isoformat()}.json"


class RepairModeRequest(BaseModel):
    mode: RepairMode
    run_date: Optional[date] = None
    tickers: list[str] = Field(default_factory=list)
    max_tickers: Optional[int] = Field(default=None, ge=1)
    max_articles: Optional[int] = Field(default=None, ge=1)
    provider_budget: dict[str, int] = Field(default_factory=dict)
    dry_run: bool = False
    reconcile_out_of_scope: bool = False

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> "RepairModeRequest":
        if self.mode == RepairMode.REPAIR_PRICE_GAPS and self.run_date is None:
            raise ValueError("repair_price_gaps requires run_date")
        return self

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, value: list[str]) -> list[str]:
        return [validate_ticker(ticker) for ticker in value]

    @field_validator("provider_budget")
    @classmethod
    def validate_provider_budget(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for provider, budget in value.items():
            provider_name = str(provider).strip().lower()
            if not provider_name:
                raise ValueError("Provider budget keys must not be empty")
            if int(budget) < 0:
                raise ValueError("Provider budget values must be non-negative")
            normalized[provider_name] = int(budget)
        return normalized


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
    logo_url: Optional[str] = Field(default=None, max_length=500)
    logo_icon_url: Optional[str] = Field(default=None, max_length=500)
    logo_source: Optional[str] = Field(default=None, max_length=100)
    logo_source_url: Optional[str] = Field(default=None, max_length=500)
    logo_checked_at: Optional[datetime] = None
    provider_symbols: dict[str, str] = Field(default_factory=dict)
    provider_symbol_sources: dict[str, str] = Field(default_factory=dict)
    provider_symbol_updated_at: Optional[datetime] = None
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


class PriceCandle(BaseModel):
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class ChartPoint(BaseModel):
    date: date
    value: Decimal


class TrendLine(BaseModel):
    start_date: date
    start_value: Decimal
    end_date: date
    end_value: Decimal
    slope_per_session: Decimal


class PriceChart(BaseModel):
    period_start: date
    period_end: date
    currency: Optional[str] = None
    candles: list[PriceCandle]
    sma_20: list[ChartPoint] = Field(default_factory=list)
    trend_line: Optional[TrendLine] = None
    support: Optional[Decimal] = None
    resistance: Optional[Decimal] = None


class RelatedNewsArticle(BaseModel):
    title: str
    source: str
    published_at: Optional[str] = None
    summary: str
    sentiment: str = "neutral"
    url: Optional[str] = None


class UpcomingTickerEvent(BaseModel):
    event_type: str
    event_date: date
    title: str
    provider: Optional[str] = None
    source_url: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class TopPick(BaseModel):
    rank: int
    ticker: str
    company_name: str
    sector: str
    logo_url: Optional[str] = None
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
    price_chart: Optional[PriceChart] = None
    related_news: list[RelatedNewsArticle] = Field(default_factory=list)
    upcoming_events: list[UpcomingTickerEvent] = Field(default_factory=list)


class SellAlert(BaseModel):
    rank: int
    ticker: str
    company_name: str
    sector: str
    logo_url: Optional[str] = None
    analysis_method: AnalysisMethod = AnalysisMethod.AI
    severity: str
    risk_level: RiskLevel
    confidence_score: int
    negative_catalyst: str
    rationale: str
    supporting_evidence: list[str]
    source_traceability: list[SignalSource]
    price_chart: Optional[PriceChart] = None
    related_news: list[RelatedNewsArticle] = Field(default_factory=list)
    upcoming_events: list[UpcomingTickerEvent] = Field(default_factory=list)


class ReviewRejection(BaseModel):
    ticker: str
    company_name: str
    sector: Optional[str] = None
    logo_url: Optional[str] = None
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
    price_chart: Optional[PriceChart] = None
    related_news: list[RelatedNewsArticle] = Field(default_factory=list)
    upcoming_events: list[UpcomingTickerEvent] = Field(default_factory=list)


class PublishedTopPicks(BaseModel):
    publication_date: date
    generated_at: datetime
    top_picks: list[TopPick]
    sell_alerts: list[SellAlert]
    review_rejections: list[ReviewRejection] = Field(default_factory=list)
    candidate_count: int
    analyzed_count: int
    data_warnings: list[str] = Field(default_factory=list)


class CollectionProviderAttempt(BaseModel):
    provider: str = Field(..., min_length=1, max_length=100)
    provider_symbol: Optional[str] = Field(default=None, max_length=50)
    status: CollectionTaskStatus
    health: Optional[CollectionTickerHealth] = None
    attempted_at: datetime
    completed_at: Optional[datetime] = None
    failure_reason: Optional[str] = Field(default=None, max_length=500)
    records_fetched: int = Field(default=0, ge=0)
    records_written: int = Field(default=0, ge=0)


class CollectionOutputCounts(BaseModel):
    records_fetched: int = Field(default=0, ge=0)
    records_written: int = Field(default=0, ge=0)
    duplicate_records: int = Field(default=0, ge=0)
    malformed_records: int = Field(default=0, ge=0)
    failed_records: int = Field(default=0, ge=0)
    successful_tickers: int = Field(default=0, ge=0)
    failed_tickers: int = Field(default=0, ge=0)
    skipped_tickers: int = Field(default=0, ge=0)


class CollectionTask(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=160)
    task_type: CollectionTaskType
    status: CollectionTaskStatus = CollectionTaskStatus.PENDING
    tickers: list[str] = Field(default_factory=list)
    ticker_range_start: Optional[str] = Field(default=None, max_length=10)
    ticker_range_end: Optional[str] = Field(default=None, max_length=10)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: Optional[str] = Field(default=None, max_length=120)
    provider: Optional[str] = Field(default=None, max_length=100)
    provider_attempts: list[CollectionProviderAttempt] = Field(default_factory=list)
    ticker_health: dict[str, CollectionTickerHealth] = Field(default_factory=dict)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    lease_owner: Optional[str] = Field(default=None, max_length=160)
    lease_expires_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failure_reason: Optional[str] = Field(default=None, max_length=500)
    output_counts: CollectionOutputCounts = Field(
        default_factory=CollectionOutputCounts
    )

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, value: list[str]) -> list[str]:
        return [validate_ticker(ticker) for ticker in value]

    @field_validator("ticker_range_start", "ticker_range_end")
    @classmethod
    def validate_ticker_range(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return validate_ticker(value)


class CollectionCoverageGate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    passed: bool
    observed_value: Decimal = Field(..., ge=0)
    required_value: Decimal = Field(..., ge=0)
    unit: str = Field(..., min_length=1, max_length=50)
    message: Optional[str] = Field(default=None, max_length=500)


class CollectionManifestSummary(BaseModel):
    total_tasks: int = Field(default=0, ge=0)
    pending_tasks: int = Field(default=0, ge=0)
    leased_tasks: int = Field(default=0, ge=0)
    running_tasks: int = Field(default=0, ge=0)
    succeeded_tasks: int = Field(default=0, ge=0)
    failed_tasks: int = Field(default=0, ge=0)
    retry_wait_tasks: int = Field(default=0, ge=0)
    skipped_tasks: int = Field(default=0, ge=0)
    total_tickers: int = Field(default=0, ge=0)
    successful_tickers: int = Field(default=0, ge=0)
    failed_tickers: int = Field(default=0, ge=0)
    coverage_ratio: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    coverage_gates: list[CollectionCoverageGate] = Field(default_factory=list)


class CollectionManifest(BaseModel):
    manifest_date: date
    schema_version: str = "1.0"
    generated_at: datetime
    updated_at: datetime
    analysis_not_before: Optional[datetime] = None
    active_ticker_count: int = Field(..., ge=0)
    task_types: list[CollectionTaskType] = Field(default_factory=list)
    tasks: list[CollectionTask] = Field(default_factory=list)
    summary: CollectionManifestSummary = Field(
        default_factory=CollectionManifestSummary
    )
    notes: list[str] = Field(default_factory=list)

    @property
    def s3_key(self) -> str:
        return collection_manifest_s3_key(self.manifest_date)
