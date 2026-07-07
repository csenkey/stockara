"""Metric models for backtest reporting."""

from decimal import Decimal

from pydantic import BaseModel, Field


class BacktestMetricSummary(BaseModel):
    portfolio_id: str
    analysis_strategy_id: str
    portfolio_policy_id: str
    initial_value: Decimal
    final_value: Decimal
    total_return: Decimal
    max_drawdown: Decimal | None = None
    trade_count: int = Field(default=0, ge=0)
    commission_paid: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    evidence_mode: str = "reduced_evidence"

