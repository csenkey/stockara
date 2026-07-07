"""Core simulation state models for offline backtesting."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InstrumentType(str, Enum):
    STOCK = "stock"
    ETF = "etf"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class RecommendationAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


class RecommendationRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class BacktestHolding(BaseModel):
    ticker: str
    quantity: int = Field(..., ge=0)
    average_cost: Decimal = Field(..., ge=Decimal("0"))
    opened_date: date
    instrument_type: InstrumentType = InstrumentType.STOCK

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker must not be empty")
        return normalized


class BacktestTransaction(BaseModel):
    transaction_id: str
    portfolio_id: str
    trade_date: date
    ticker: str
    action: TradeAction
    quantity: int = Field(..., ge=0)
    price: Decimal = Field(..., ge=Decimal("0"))
    gross_value: Decimal = Field(..., ge=Decimal("0"))
    commission: Decimal = Field(..., ge=Decimal("0"))
    cash_after: Decimal = Field(..., ge=Decimal("0"))
    analysis_strategy_id: str | None = None
    recommendation_id: str | None = None
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()


class BacktestSnapshot(BaseModel):
    portfolio_id: str
    snapshot_date: date
    cash: Decimal = Field(..., ge=Decimal("0"))
    holdings_value: Decimal = Field(..., ge=Decimal("0"))
    total_value: Decimal = Field(..., ge=Decimal("0"))
    realized_gain_loss: Decimal = Decimal("0")
    unrealized_gain_loss: Decimal = Decimal("0")
    commission_paid_to_date: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    data_quality: str = "complete"
    missing_prices: list[str] = Field(default_factory=list)


class BacktestPortfolio(BaseModel):
    portfolio_id: str
    portfolio_policy_id: str
    initial_allocation_method: str
    cash: Decimal = Field(..., ge=Decimal("0"))
    holdings: dict[str, BacktestHolding] = Field(default_factory=dict)
    transactions: list[BacktestTransaction] = Field(default_factory=list)
    snapshots: list[BacktestSnapshot] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _holdings_keyed_by_ticker(self) -> "BacktestPortfolio":
        for ticker, holding in self.holdings.items():
            if ticker.upper() != holding.ticker:
                raise ValueError("holdings must be keyed by normalized ticker")
        return self

    def clone_for_shadow(self, portfolio_id: str) -> "BacktestPortfolio":
        """Return a deep copy with a new identifier and no shadow transactions."""
        data = self.model_dump()
        data["portfolio_id"] = portfolio_id
        data["transactions"] = []
        data["snapshots"] = []
        return BacktestPortfolio.model_validate(data)


class DecisionShadow(BaseModel):
    shadow_id: str
    parent_portfolio_id: str
    shadow_portfolio_id: str
    forked_at: date
    triggering_transaction_id: str | None = None
    ignored_action: TradeAction | None = None
    evaluation_windows_days: list[int] = Field(default_factory=lambda: [7, 30, 90, 180, 365])
    recursive_shadows_enabled: bool = False
    status: str = "active"


class BacktestRunSummary(BaseModel):
    run_id: str
    start_date: date
    end_date: date
    portfolio_count: int = Field(..., ge=0)
    analysis_strategy_ids: list[str] = Field(default_factory=list)
    portfolio_policy_ids: list[str] = Field(default_factory=list)
    baseline_etfs: list[str] = Field(default_factory=list)
    evidence_mode: str = "reduced_evidence"
    recommendation_cache_policy: str = "fixture_only"
    artifact_prefix: str
    limitations: list[str] = Field(default_factory=list)


class ReplayRecommendation(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    recommendation_id: str
    analysis_strategy_id: str
    recommendation_date: date
    ticker: str
    action: RecommendationAction
    risk: RecommendationRisk
    confidence: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))
    score: Decimal | None = None
    ai_review_status: str | None = None
    evidence_coverage: str = "unknown"
    prompt_template_version: str | None = None
    model_id: str | None = None
    evidence_hash: str | None = None
    publication_status: str | None = None
    reasoning: str | None = None
