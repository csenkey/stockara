"""Demo Account Manager service for creating and managing simulated trading accounts."""

import math
import random
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from psycopg2.extras import RealDictCursor

import structlog

from backend.src.db.connection import get_db_connection
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

# Constants
INITIAL_BANKROLL = Decimal("10000.00")
MIN_CASH = Decimal("500.00")
MAX_CASH = Decimal("9500.00")
COMMISSION_RATE = Decimal("0.01")
MIN_ACTIVE_STOCKS = 10


class DemoAccountManager:
    """Manages creation, storage, and querying of demo trading accounts."""

    async def create_accounts(self, count: int = 100) -> list[DemoAccount]:
        """Create demo accounts with random initial allocations.

        Each account starts with exactly $10,000, split between cash ($500-$9500)
        and initial stock purchases from the active watchlist with 1% commission.

        Args:
            count: Number of accounts to create (default 100).

        Returns:
            List of created DemoAccount instances.

        Raises:
            ValueError: If fewer than 10 active stocks exist in the watchlist.
        """
        # Fetch active stocks from the watchlist
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

        names = SUPERHERO_NAMES[:count]
        created_accounts: list[DemoAccount] = []

        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                for name in names:
                    account = await self._create_single_account(
                        cur, name, active_stocks
                    )
                    created_accounts.append(account)

        logger.info(
            "Demo accounts created successfully",
            count=len(created_accounts),
        )
        return created_accounts

    async def _get_active_stocks(self) -> list[dict]:
        """Fetch active stocks from the watchlist with their latest closing prices.

        Returns:
            List of dicts with 'ticker' and 'close_price' keys.
        """
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT s.ticker, sd.close_price
                    FROM stocks s
                    JOIN LATERAL (
                        SELECT close_price
                        FROM stock_data sd
                        WHERE sd.ticker = s.ticker
                        ORDER BY date DESC
                        LIMIT 1
                    ) sd ON TRUE
                    WHERE s.is_active = TRUE
                    AND sd.close_price IS NOT NULL
                    """
                )
                rows = cur.fetchall()
        return [{"ticker": row["ticker"], "close_price": Decimal(str(row["close_price"]))} for row in rows]

    async def _create_single_account(
        self, cur, name: str, active_stocks: list[dict]
    ) -> DemoAccount:
        """Create a single demo account with random allocation.

        The allocation ensures: cash + sum(qty * price * 1.01) == $10,000
        """
        # Random cash between $500 and $9500
        cash_cents = random.randint(50000, 950000)
        cash_balance = Decimal(cash_cents) / Decimal("100")

        # Budget available for stock purchases (including commission)
        stock_budget = INITIAL_BANKROLL - cash_balance

        # Insert the account
        cur.execute(
            """
            INSERT INTO demo_accounts (account_name, cash_balance)
            VALUES (%s, %s)
            RETURNING id, account_name, cash_balance, created_at
            """,
            (name, cash_balance),
        )
        account_row = cur.fetchone()
        account_id = account_row["id"]

        # Allocate stock purchases from active watchlist
        await self._allocate_initial_stocks(cur, account_id, stock_budget, active_stocks)

        return DemoAccount(
            id=account_row["id"],
            account_name=account_row["account_name"],
            cash_balance=account_row["cash_balance"],
            created_at=account_row["created_at"],
        )

    async def _allocate_initial_stocks(
        self, cur, account_id: int, budget: Decimal, active_stocks: list[dict]
    ) -> None:
        """Allocate the stock budget across random stocks from the active watchlist.

        Each purchase includes 1% commission. The total spent (qty * price * 1.01)
        must not exceed the available budget.
        """
        if budget <= Decimal("0"):
            return

        remaining_budget = budget
        # Shuffle stocks to randomize selection
        shuffled_stocks = random.sample(active_stocks, len(active_stocks))

        for stock in shuffled_stocks:
            if remaining_budget <= Decimal("0"):
                break

            ticker = stock["ticker"]
            price = stock["close_price"]

            # Cost per share including commission: price * 1.01
            cost_per_share = price * (Decimal("1") + COMMISSION_RATE)

            if cost_per_share > remaining_budget:
                continue

            # Calculate max whole shares we can afford
            max_shares = int((remaining_budget / cost_per_share).to_integral_value(rounding=ROUND_DOWN))
            if max_shares <= 0:
                continue

            # Buy a random number of shares (at least 1, at most max_shares)
            quantity = random.randint(1, max_shares)

            # Total cost including commission
            total_value = price * Decimal(quantity)
            commission = (total_value * COMMISSION_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_cost = total_value + commission

            # Ensure we don't overshoot the budget
            if total_cost > remaining_budget:
                # Reduce quantity to fit
                quantity = int((remaining_budget / cost_per_share).to_integral_value(rounding=ROUND_DOWN))
                if quantity <= 0:
                    continue
                total_value = price * Decimal(quantity)
                commission = (total_value * COMMISSION_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                total_cost = total_value + commission

            remaining_budget -= total_cost

            # Insert holding
            cur.execute(
                """
                INSERT INTO demo_holdings (account_id, ticker, quantity, purchase_price)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (account_id, ticker) DO UPDATE
                SET quantity = demo_holdings.quantity + EXCLUDED.quantity
                """,
                (account_id, ticker, quantity, price),
            )

            # Record the initial purchase transaction
            cash_after = (INITIAL_BANKROLL - budget) + remaining_budget
            cur.execute(
                """
                INSERT INTO demo_transactions
                    (account_id, ticker, action, quantity, price_per_share,
                     total_value, commission_fee, cash_after)
                VALUES (%s, %s, 'BUY', %s, %s, %s, %s, %s)
                """,
                (account_id, ticker, quantity, price, total_value, commission, cash_after),
            )

        # Update cash balance to reflect actual remaining amount after purchases
        final_cash = (INITIAL_BANKROLL - budget) + remaining_budget
        cur.execute(
            """
            UPDATE demo_accounts SET cash_balance = %s WHERE id = %s
            """,
            (final_cash, account_id),
        )

    async def get_account(self, name: str) -> DemoAccount | None:
        """Retrieve a single demo account by superhero name.

        Args:
            name: The superhero name of the account.

        Returns:
            DemoAccount if found, None otherwise.
        """
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, account_name, cash_balance, created_at
                    FROM demo_accounts
                    WHERE account_name = %s
                    """,
                    (name,),
                )
                row = cur.fetchone()

        if row is None:
            return None

        return DemoAccount(
            id=row["id"],
            account_name=row["account_name"],
            cash_balance=row["cash_balance"],
            created_at=row["created_at"],
        )

    async def get_leaderboard(self) -> list[LeaderboardEntry]:
        """Get all accounts ranked by portfolio value descending.

        Portfolio value = cash_balance + sum(holdings quantity * current closing price).

        Returns:
            List of LeaderboardEntry sorted by portfolio_value descending.
        """
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    WITH portfolio_values AS (
                        SELECT
                            da.id,
                            da.account_name,
                            da.cash_balance,
                            COALESCE(SUM(dh.quantity * (
                                SELECT sd.close_price
                                FROM stock_data sd
                                WHERE sd.ticker = dh.ticker
                                ORDER BY sd.date DESC
                                LIMIT 1
                            )), 0) AS holdings_value
                        FROM demo_accounts da
                        LEFT JOIN demo_holdings dh ON da.id = dh.account_id
                        GROUP BY da.id, da.account_name, da.cash_balance
                    ),
                    transaction_counts AS (
                        SELECT account_id, COUNT(*) AS txn_count
                        FROM demo_transactions
                        GROUP BY account_id
                    )
                    SELECT
                        pv.id,
                        pv.account_name,
                        pv.cash_balance,
                        (pv.cash_balance + pv.holdings_value) AS portfolio_value,
                        COALESCE(tc.txn_count, 0) AS transaction_count
                    FROM portfolio_values pv
                    LEFT JOIN transaction_counts tc ON pv.id = tc.account_id
                    ORDER BY portfolio_value DESC
                    """
                )
                rows = cur.fetchall()

                # Get sparkline data (last 30 days) for each account
                result: list[LeaderboardEntry] = []
                for rank, row in enumerate(rows, start=1):
                    portfolio_value = Decimal(str(row["portfolio_value"]))
                    gain_loss_pct = ((portfolio_value - INITIAL_BANKROLL) / INITIAL_BANKROLL * Decimal("100")).quantize(Decimal("0.01"))

                    # Fetch sparkline data
                    cur.execute(
                        """
                        SELECT portfolio_value
                        FROM demo_daily_snapshots
                        WHERE account_id = %s
                        ORDER BY snapshot_date DESC
                        LIMIT 30
                        """,
                        (row["id"],),
                    )
                    sparkline_rows = cur.fetchall()
                    sparkline_data = [Decimal(str(r["portfolio_value"])) for r in reversed(sparkline_rows)]

                    result.append(
                        LeaderboardEntry(
                            rank=rank,
                            account_name=row["account_name"],
                            portfolio_value=portfolio_value,
                            cash_balance=Decimal(str(row["cash_balance"])),
                            gain_loss_pct=gain_loss_pct,
                            transaction_count=int(row["transaction_count"]),
                            sparkline_data=sparkline_data,
                        )
                    )

        return result

    async def get_transactions(
        self, name: str, page: int = 1, page_size: int = 20
    ) -> PaginatedTransactionsResponse | None:
        """Get paginated transaction history for an account.

        Args:
            name: The superhero name of the account.
            page: Page number (1-indexed).
            page_size: Number of transactions per page.

        Returns:
            PaginatedTransactionsResponse if account found, None otherwise.
        """
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get account ID
                cur.execute(
                    "SELECT id FROM demo_accounts WHERE account_name = %s",
                    (name,),
                )
                account_row = cur.fetchone()
                if account_row is None:
                    return None

                account_id = account_row["id"]

                # Get total count
                cur.execute(
                    "SELECT COUNT(*) AS total FROM demo_transactions WHERE account_id = %s",
                    (account_id,),
                )
                total = cur.fetchone()["total"]

                # Get paginated transactions
                offset = (page - 1) * page_size
                cur.execute(
                    """
                    SELECT id, ticker, action, quantity, price_per_share,
                           total_value, commission_fee, cash_after, executed_at
                    FROM demo_transactions
                    WHERE account_id = %s
                    ORDER BY executed_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (account_id, page_size, offset),
                )
                rows = cur.fetchall()

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
        """Get daily portfolio value time series for an account.

        Args:
            name: The superhero name of the account.

        Returns:
            List of DailySnapshot sorted by date ascending, or None if account not found.
        """
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id FROM demo_accounts WHERE account_name = %s",
                    (name,),
                )
                account_row = cur.fetchone()
                if account_row is None:
                    return None

                account_id = account_row["id"]

                cur.execute(
                    """
                    SELECT snapshot_date, portfolio_value, cash_balance, holdings_value
                    FROM demo_daily_snapshots
                    WHERE account_id = %s
                    ORDER BY snapshot_date ASC
                    """,
                    (account_id,),
                )
                rows = cur.fetchall()

        return [
            DailySnapshot(
                snapshot_date=row["snapshot_date"],
                portfolio_value=Decimal(str(row["portfolio_value"])),
                cash_balance=Decimal(str(row["cash_balance"])),
                holdings_value=Decimal(str(row["holdings_value"])),
            )
            for row in rows
        ]

    async def record_transaction(self, account_id: int, txn: dict) -> None:
        """Store a transaction record.

        Args:
            account_id: The demo account ID.
            txn: Dict with keys: ticker, action, quantity, price_per_share,
                 total_value, commission_fee, cash_after.
        """
        async with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO demo_transactions
                        (account_id, ticker, action, quantity, price_per_share,
                         total_value, commission_fee, cash_after)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        account_id,
                        txn["ticker"],
                        txn["action"],
                        txn["quantity"],
                        txn["price_per_share"],
                        txn["total_value"],
                        txn["commission_fee"],
                        txn["cash_after"],
                    ),
                )

    async def take_daily_snapshot(self, account_name: str, snapshot_date: date) -> None:
        """Record end-of-day portfolio snapshot for an account.

        Calculates the current portfolio value based on cash + holdings at
        latest closing prices.

        Args:
            account_name: The superhero name of the account.
            snapshot_date: The date for the snapshot.
        """
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get account
                cur.execute(
                    "SELECT id, cash_balance FROM demo_accounts WHERE account_name = %s",
                    (account_name,),
                )
                account_row = cur.fetchone()
                if account_row is None:
                    logger.warning(
                        "Cannot take snapshot for non-existent account",
                        account_name=account_name,
                    )
                    return

                account_id = account_row["id"]
                cash_balance = Decimal(str(account_row["cash_balance"]))

                # Calculate holdings value
                cur.execute(
                    """
                    SELECT COALESCE(SUM(dh.quantity * (
                        SELECT sd.close_price
                        FROM stock_data sd
                        WHERE sd.ticker = dh.ticker
                        ORDER BY sd.date DESC
                        LIMIT 1
                    )), 0) AS holdings_value
                    FROM demo_holdings dh
                    WHERE dh.account_id = %s
                    """,
                    (account_id,),
                )
                holdings_row = cur.fetchone()
                holdings_value = Decimal(str(holdings_row["holdings_value"]))
                portfolio_value = cash_balance + holdings_value

                # Insert or update snapshot
                cur.execute(
                    """
                    INSERT INTO demo_daily_snapshots
                        (account_id, snapshot_date, portfolio_value, cash_balance, holdings_value)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (account_id, snapshot_date)
                    DO UPDATE SET
                        portfolio_value = EXCLUDED.portfolio_value,
                        cash_balance = EXCLUDED.cash_balance,
                        holdings_value = EXCLUDED.holdings_value
                    """,
                    (account_id, snapshot_date, portfolio_value, cash_balance, holdings_value),
                )
