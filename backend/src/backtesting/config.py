"""Backtest run configuration models.

The first milestone is intentionally offline and cache-backed. A config may
describe AI-backed strategies, but execution must use stored recommendation
artifacts until live replay is explicitly added.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ExecutionTiming(str, Enum):
    """When simulated orders execute relative to a recommendation."""

    NEXT_OPEN = "next_open"
    SAME_CLOSE = "same_close"


class RecommendationCachePolicy(str, Enum):
    """Controls whether the replay layer may call external AI providers."""

    FIXTURE_ONLY = "fixture_only"
    S3_CACHE_ONLY = "s3_cache_only"


class EvidenceMode(str, Enum):
    """How complete the historical evidence is for a run."""

    DECISION_GRADE = "decision_grade"
    REDUCED_EVIDENCE = "reduced_evidence"


class DataSourceSet(BaseModel):
    """Historical data source declarations copied into run artifacts."""

    ohlcv: list[str] = Field(default_factory=list)
    etf_prices: list[str] = Field(default_factory=list)
    news: list[str] = Field(default_factory=list)
    earnings: list[str] = Field(default_factory=list)
    dividends: list[str] = Field(default_factory=list)
    instruments: list[str] = Field(default_factory=list)


class BacktestConfig(BaseModel):
    """Configuration for a single immutable backtest run."""

    run_id: str = Field(default_factory=lambda: f"bt_{uuid4().hex}")
    start_date: date = date(2022, 1, 1)
    end_date: date = date(2022, 12, 31)
    initial_capital: Decimal = Decimal("10000.00")
    portfolio_count: int = Field(default=20, ge=1, le=1000)
    random_seed: int = 20220101
    commission_rate: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0"), le=Decimal("1"))
    execution_timing: ExecutionTiming = ExecutionTiming.NEXT_OPEN
    analysis_strategy_ids: list[str] = Field(default_factory=lambda: ["analysis_strategy_current"])
    portfolio_policy_ids: list[str] = Field(
        default_factory=lambda: ["conservative", "balanced", "aggressive"]
    )
    baseline_etfs: list[str] = Field(default_factory=lambda: ["SPY"])
    shadow_windows_days: list[int] = Field(default_factory=lambda: [7, 30, 90, 180, 365])
    material_decision_threshold: Decimal = Field(default=Decimal("0.02"), ge=Decimal("0"))
    s3_prefix: str = "backtests"
    data_sources: DataSourceSet = Field(default_factory=DataSourceSet)
    analysis_strategy_registry: str = "docs/steering/analysis-strategies/strategy_registry.md"
    recommendation_cache_policy: RecommendationCachePolicy = RecommendationCachePolicy.FIXTURE_ONLY
    evidence_mode: EvidenceMode = EvidenceMode.REDUCED_EVIDENCE

    @field_validator("analysis_strategy_ids", "portfolio_policy_ids", "baseline_etfs")
    @classmethod
    def _non_empty_list(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("must contain at least one value")
        return value

    @field_validator("s3_prefix")
    @classmethod
    def _normalize_s3_prefix(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if not normalized:
            raise ValueError("s3_prefix must not be empty")
        return normalized

    @field_validator("shadow_windows_days")
    @classmethod
    def _positive_unique_windows(cls, value: list[int]) -> list[int]:
        if any(days <= 0 for days in value):
            raise ValueError("shadow windows must be positive")
        return sorted(set(value))

    @model_validator(mode="after")
    def _validate_date_range(self) -> "BacktestConfig":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self

