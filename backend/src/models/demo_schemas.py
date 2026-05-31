"""Pydantic models and schemas for the Demo Trading Accounts feature."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class DemoAccount(BaseModel):
    """A simulated demo trading account."""

    id: int
    account_name: str
    cash_balance: Decimal
    created_at: datetime


class DemoHolding(BaseModel):
    """A stock holding within a demo account."""

    ticker: str
    quantity: int
    purchase_price: Decimal
    current_price: Decimal | None = None
    unrealized_gain_loss: Decimal | None = None


class DemoTransaction(BaseModel):
    """A recorded transaction (buy or sell) in a demo account."""

    id: int
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: int
    price_per_share: Decimal
    total_value: Decimal
    commission_fee: Decimal
    cash_after: Decimal
    executed_at: datetime


class DailySnapshot(BaseModel):
    """End-of-day portfolio snapshot for time-series charting."""

    snapshot_date: date
    portfolio_value: Decimal
    cash_balance: Decimal
    holdings_value: Decimal


class AllocationEntry(BaseModel):
    """A single entry in the portfolio allocation pie chart."""

    label: str  # ticker or "Cash"
    value: Decimal
    percentage: Decimal


class LeaderboardEntry(BaseModel):
    """A single row on the demo trading leaderboard."""

    rank: int
    account_name: str
    portfolio_value: Decimal
    cash_balance: Decimal
    gain_loss_pct: Decimal
    transaction_count: int
    sparkline_data: list[Decimal] = Field(
        default_factory=list, description="Last 30 days of portfolio values"
    )


class LeaderboardResponse(BaseModel):
    """Response model for the leaderboard endpoint."""

    entries: list[LeaderboardEntry]
    last_updated: datetime


class AccountDetailResponse(BaseModel):
    """Response model for the account detail endpoint."""

    account_name: str
    portfolio_value: Decimal
    cash_balance: Decimal
    total_gain_loss: Decimal
    gain_loss_pct: Decimal
    holdings: list[DemoHolding]
    allocation: list[AllocationEntry]  # for pie chart


class PaginatedTransactionsResponse(BaseModel):
    """Response model for paginated transaction history."""

    transactions: list[DemoTransaction]
    total: int
    page: int
    page_size: int
    total_pages: int


class PerformanceResponse(BaseModel):
    """Response model for the performance time-series endpoint."""

    account_name: str
    data_points: list[DailySnapshot]
    initial_value: Decimal = Decimal("10000.00")  # always $10,000
