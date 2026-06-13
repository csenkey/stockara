"""Demo Account Manager service for DynamoDB-backed simulated trading accounts."""

import math
import random
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

import structlog

from backend.src.db.connection import store
from backend.src.models.demo_schemas import (
    DailySnapshot,
    DemoAccount,
    DemoHolding,
    DemoTransaction,
    LeaderboardEntry,
    PaginatedTransactionsResponse,
)
from backend.src.services.demo_superhero_names import SUPERHERO_NAMES

logger = structlog.get_logger(__name__)

INITIAL_BANKROLL = Decimal("10000.00")
MIN_CASH = Decimal("500.00")
MAX_CASH = Decimal("9500.00")
COMMISSION_RATE = Decimal("0.01")
MIN_ACTIVE_STOCKS = 10


class DemoAccountManager:
    """Manages creation, storage, and querying of demo trading accounts."""

    async def create_accounts(self, count: int = 100) -> list[DemoAccount]:
        active_stocks = await self._get_active_stocks()
        if len(active_stocks) < MIN_ACTIVE_STOCKS:
            logger.error(
                "Insufficient active stocks for demo account creation",
                active_count=len(active_stocks),
                required=MIN_ACTIVE_STOCKS,
            )
            raise ValueError(
                f"Cannot create demo accounts: only {len(active_stocks)} active stocks "
                f"available, minimum {MIN_ACTIVE_STOCKS} required."
            )

        created_accounts: list[DemoAccount] = []
        for name in SUPERHERO_NAMES[:count]:
            created_accounts.append(await self._create_single_account(name, active_stocks))

        logger.info("Demo accounts created successfully", count=len(created_accounts))
        return created_accounts

    async def _get_active_stocks(self) -> list[dict]:
        latest_prices = store.latest_prices()
        return [
            {"ticker": stock["ticker"], "close_price": latest_prices[stock["ticker"]]}
            for stock in store.list_stocks(is_active=True)
            if stock["ticker"] in latest_prices
        ]

    async def _create_single_account(
        self, name: str, active_stocks: list[dict]
    ) -> DemoAccount:
        cash_cents = random.randint(50000, 950000)
        cash_balance = Decimal(cash_cents) / Decimal("100")
        stock_budget = INITIAL_BANKROLL - cash_balance

        account_row = store.create_demo_account(name, cash_balance)
        await self._allocate_initial_stocks(account_row["id"], stock_budget, active_stocks)

        updated_account = store.get_demo_account_by_name(name) or account_row
        return DemoAccount(
            id=updated_account["id"],
            account_name=updated_account["account_name"],
            cash_balance=Decimal(str(updated_account["cash_balance"])),
            created_at=updated_account["created_at"],
        )

    async def _allocate_initial_stocks(
        self, account_id: str, budget: Decimal, active_stocks: list[dict]
    ) -> None:
        if budget <= Decimal("0"):
            return

        remaining_budget = budget
        shuffled_stocks = random.sample(active_stocks, len(active_stocks))

        for stock in shuffled_stocks:
            if remaining_budget <= Decimal("0"):
                break

            ticker = stock["ticker"]
            price = Decimal(str(stock["close_price"]))
            cost_per_share = price * (Decimal("1") + COMMISSION_RATE)
            if cost_per_share > remaining_budget:
                continue

            max_shares = int(
                (remaining_budget / cost_per_share).to_integral_value(rounding=ROUND_DOWN)
            )
            if max_shares <= 0:
                continue

            quantity = random.randint(1, max_shares)
            total_value = price * Decimal(quantity)
            commission = (total_value * COMMISSION_RATE).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_cost = total_value + commission

            if total_cost > remaining_budget:
                quantity = int(
                    (remaining_budget / cost_per_share).to_integral_value(rounding=ROUND_DOWN)
                )
                if quantity <= 0:
                    continue
                total_value = price * Decimal(quantity)
                commission = (total_value * COMMISSION_RATE).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                total_cost = total_value + commission

            remaining_budget -= total_cost
            cash_after = (INITIAL_BANKROLL - budget) + remaining_budget

            store.upsert_demo_holding(account_id, ticker, quantity, price)
            store.put_demo_transaction(
                account_id,
                {
                    "ticker": ticker,
                    "action": "BUY",
                    "quantity": quantity,
                    "price_per_share": price,
                    "total_value": total_value,
                    "commission_fee": commission,
                    "cash_after": cash_after,
                },
            )

        final_cash = (INITIAL_BANKROLL - budget) + remaining_budget
        store.update_demo_cash(account_id, final_cash)

    async def get_account(self, name: str) -> DemoAccount | None:
        row = store.get_demo_account_by_name(name)
        if row is None:
            return None
        return DemoAccount(
            id=row["id"],
            account_name=row["account_name"],
            cash_balance=Decimal(str(row["cash_balance"])),
            created_at=row["created_at"],
        )

    async def get_leaderboard(self) -> list[LeaderboardEntry]:
        latest_prices = store.latest_prices()
        rows = []
        for account in store.list_demo_accounts():
            cash_balance = Decimal(str(account["cash_balance"]))
            holdings = store.list_demo_holdings(account["id"])
            holdings_value = sum(
                Decimal(str(h["quantity"])) * latest_prices.get(h["ticker"], Decimal("0"))
                for h in holdings
            )
            transactions = store.list_demo_transactions(account["id"])
            portfolio_value = cash_balance + holdings_value
            rows.append(
                {
                    "account": account,
                    "cash_balance": cash_balance,
                    "portfolio_value": portfolio_value,
                    "transaction_count": len(transactions),
                }
            )

        rows.sort(key=lambda r: r["portfolio_value"], reverse=True)
        result: list[LeaderboardEntry] = []
        for rank, row in enumerate(rows, start=1):
            portfolio_value = row["portfolio_value"]
            gain_loss_pct = (
                (portfolio_value - INITIAL_BANKROLL) / INITIAL_BANKROLL * Decimal("100")
            ).quantize(Decimal("0.01"))
            snapshots = store.list_demo_snapshots(row["account"]["id"])[-30:]
            result.append(
                LeaderboardEntry(
                    rank=rank,
                    account_name=row["account"]["account_name"],
                    portfolio_value=portfolio_value,
                    cash_balance=row["cash_balance"],
                    gain_loss_pct=gain_loss_pct,
                    transaction_count=row["transaction_count"],
                    sparkline_data=[
                        Decimal(str(snapshot["portfolio_value"])) for snapshot in snapshots
                    ],
                )
            )
        return result

    async def get_transactions(
        self, name: str, page: int = 1, page_size: int = 20
    ) -> PaginatedTransactionsResponse | None:
        account = store.get_demo_account_by_name(name)
        if account is None:
            return None

        all_rows = store.list_demo_transactions(account["id"])
        total = len(all_rows)
        offset = (page - 1) * page_size
        rows = all_rows[offset : offset + page_size]

        transactions = [
            DemoTransaction(
                id=row["id"],
                ticker=row["ticker"],
                action=row["action"],
                quantity=row["quantity"],
                price_per_share=Decimal(str(row["price_per_share"])),
                total_value=Decimal(str(row["total_value"])),
                commission_fee=Decimal(str(row["commission_fee"])),
                cash_after=Decimal(str(row["cash_after"])),
                executed_at=row["executed_at"],
            )
            for row in rows
        ]

        total_pages = math.ceil(total / page_size) if page_size > 0 else 0
        return PaginatedTransactionsResponse(
            transactions=transactions,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def get_performance_series(self, name: str) -> list[DailySnapshot] | None:
        account = store.get_demo_account_by_name(name)
        if account is None:
            return None
        return [
            DailySnapshot(
                snapshot_date=row["snapshot_date"],
                portfolio_value=Decimal(str(row["portfolio_value"])),
                cash_balance=Decimal(str(row["cash_balance"])),
                holdings_value=Decimal(str(row["holdings_value"])),
            )
            for row in store.list_demo_snapshots(account["id"])
        ]

    async def record_transaction(self, account_id: str, txn: dict) -> None:
        store.put_demo_transaction(account_id, txn)

    async def take_daily_snapshot(self, account_name: str, snapshot_date: date) -> None:
        account = store.get_demo_account_by_name(account_name)
        if account is None:
            logger.warning(
                "Cannot take snapshot for non-existent account",
                account_name=account_name,
            )
            return

        cash_balance = Decimal(str(account["cash_balance"]))
        latest_prices = store.latest_prices()
        holdings_value = sum(
            Decimal(str(holding["quantity"]))
            * latest_prices.get(holding["ticker"], Decimal("0"))
            for holding in store.list_demo_holdings(account["id"])
        )
        portfolio_value = cash_balance + holdings_value
        store.put_demo_snapshot(
            account["id"],
            snapshot_date,
            portfolio_value,
            cash_balance,
            holdings_value,
        )
