"""FastAPI router for public demo trading account endpoints."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

import structlog

from backend.src.models.demo_schemas import (
    AccountDetailResponse,
    AllocationEntry,
    DemoHolding,
    LeaderboardResponse,
    PaginatedTransactionsResponse,
    PerformanceResponse,
)
from backend.src.services.demo_account_manager import DemoAccountManager

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])

# Shared service instance
_manager = DemoAccountManager()


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard():
    """Return all 100 demo accounts ranked by portfolio value.

    Public endpoint — no authentication required.
    """
    entries = await _manager.get_leaderboard()
    return LeaderboardResponse(
        entries=entries,
        last_updated=datetime.utcnow(),
    )


@router.get("/accounts/{name}", response_model=AccountDetailResponse)
async def get_account_detail(name: str):
    """Return account detail with holdings and allocation for a demo account.

    Public endpoint — no authentication required.
    """
    account = await _manager.get_account(name)
    if account is None:
        raise HTTPException(
            status_code=404,
            detail=f"Demo account '{name}' not found",
        )

    # Get holdings with current prices and unrealized P&L
    holdings = await _get_holdings_with_prices(account.id)

    # Calculate portfolio value
    holdings_value = sum(
        (h.current_price or h.purchase_price) * h.quantity for h in holdings
    )
    portfolio_value = account.cash_balance + holdings_value
    total_gain_loss = portfolio_value - Decimal("10000.00")
    gain_loss_pct = (total_gain_loss / Decimal("10000.00") * Decimal("100")).quantize(
        Decimal("0.01")
    )

    # Build allocation entries for pie chart
    allocation = _build_allocation(holdings, account.cash_balance, portfolio_value)

    return AccountDetailResponse(
        account_name=account.account_name,
        portfolio_value=portfolio_value,
        cash_balance=account.cash_balance,
        total_gain_loss=total_gain_loss,
        gain_loss_pct=gain_loss_pct,
        holdings=holdings,
        allocation=allocation,
    )


@router.get("/accounts/{name}/transactions", response_model=PaginatedTransactionsResponse)
async def get_transactions(
    name: str,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """Return paginated transaction history for a demo account.

    Public endpoint — no authentication required.
    """
    result = await _manager.get_transactions(name, page=page, page_size=page_size)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Demo account '{name}' not found",
        )
    return result


@router.get("/accounts/{name}/performance", response_model=PerformanceResponse)
async def get_performance(name: str):
    """Return daily portfolio value time series for a demo account.

    Public endpoint — no authentication required.
    """
    data_points = await _manager.get_performance_series(name)
    if data_points is None:
        raise HTTPException(
            status_code=404,
            detail=f"Demo account '{name}' not found",
        )
    return PerformanceResponse(
        account_name=name,
        data_points=data_points,
        initial_value=Decimal("10000.00"),
    )


# --- Helpers ---


async def _get_holdings_with_prices(account_id: int) -> list[DemoHolding]:
    """Fetch holdings for an account with current prices and unrealized P&L."""
    from psycopg2.extras import RealDictCursor

    from backend.src.db.connection import get_db_connection

    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    dh.ticker,
                    dh.quantity,
                    dh.purchase_price,
                    (
                        SELECT sd.close_price
                        FROM stock_data sd
                        WHERE sd.ticker = dh.ticker
                        ORDER BY sd.date DESC
                        LIMIT 1
                    ) AS current_price
                FROM demo_holdings dh
                WHERE dh.account_id = %s
                ORDER BY dh.ticker
                """,
                (account_id,),
            )
            rows = cur.fetchall()

    holdings = []
    for row in rows:
        purchase_price = Decimal(str(row["purchase_price"]))
        current_price = Decimal(str(row["current_price"])) if row["current_price"] else None
        quantity = row["quantity"]

        unrealized_gain_loss = None
        if current_price is not None:
            unrealized_gain_loss = (current_price - purchase_price) * quantity

        holdings.append(
            DemoHolding(
                ticker=row["ticker"],
                quantity=quantity,
                purchase_price=purchase_price,
                current_price=current_price,
                unrealized_gain_loss=unrealized_gain_loss,
            )
        )

    return holdings


def _build_allocation(
    holdings: list[DemoHolding], cash_balance: Decimal, portfolio_value: Decimal
) -> list[AllocationEntry]:
    """Build allocation entries for the portfolio pie chart."""
    if portfolio_value == 0:
        return []

    entries: list[AllocationEntry] = []

    # Add cash entry
    cash_pct = (cash_balance / portfolio_value * Decimal("100")).quantize(Decimal("0.01"))
    entries.append(
        AllocationEntry(label="Cash", value=cash_balance, percentage=cash_pct)
    )

    # Add each holding
    for h in holdings:
        price = h.current_price or h.purchase_price
        value = price * h.quantity
        pct = (value / portfolio_value * Decimal("100")).quantize(Decimal("0.01"))
        entries.append(AllocationEntry(label=h.ticker, value=value, percentage=pct))

    return entries
