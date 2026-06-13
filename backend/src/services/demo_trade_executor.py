"""Demo Trade Executor service for executing daily simulated trades based on AI recommendations."""

from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from dataclasses import dataclass, field

import structlog

from backend.src.db.connection import store
from backend.src.services.demo_account_manager import DemoAccountManager

logger = structlog.get_logger(__name__)

# Constants
COMMISSION_RATE = Decimal("0.01")
MAX_BUY_ALLOCATION_PCT = Decimal("0.10")  # 10% of portfolio value per buy
BATCH_SIZE = 25


@dataclass
class ExecutionSummary:
    """Summary of a daily trade execution run."""

    accounts_processed: int = 0
    buys_executed: int = 0
    sells_executed: int = 0
    skipped_insufficient_cash: int = 0
    skipped_no_price: int = 0
    errors: list[str] = field(default_factory=list)


class DemoTradeExecutor:
    """Executes daily trades for demo accounts based on AI recommendations.

    Triggered daily via EventBridge after AI analysis completes.
    Processes all 100 demo accounts, evaluating each stock recommendation
    and executing buy/sell orders with 1% commission.
    """

    def __init__(self):
        self.account_manager = DemoAccountManager()

    async def execute_daily_trades(self) -> ExecutionSummary:
        """Run trading logic for all 100 accounts.

        Fetches the latest AI recommendations and closing prices, then
        processes each account in batches of 25. Commits after each batch
        for partial progress on timeout.

        Returns:
            ExecutionSummary with counts of actions taken.
        """
        summary = ExecutionSummary()
        today = date.today()

        # Fetch latest AI recommendations
        recommendations = await self._get_latest_recommendations()
        if not recommendations:
            logger.warning("No AI recommendations available, skipping daily trades")
            return summary

        # Fetch latest closing prices for all tickers
        prices = await self._get_latest_prices()
        if not prices:
            logger.warning("No stock prices available, skipping daily trades")
            return summary

        # Fetch all demo accounts
        accounts = await self._get_all_accounts()
        if not accounts:
            logger.warning("No demo accounts found, skipping daily trades")
            return summary

        # Process accounts in batches of 25
        for batch_start in range(0, len(accounts), BATCH_SIZE):
            batch = accounts[batch_start:batch_start + BATCH_SIZE]

            for account in batch:
                try:
                    batch_summary = await self._evaluate_account(
                        account, recommendations, prices
                    )
                    summary.buys_executed += batch_summary["buys"]
                    summary.sells_executed += batch_summary["sells"]
                    summary.skipped_insufficient_cash += batch_summary["skipped_cash"]
                    summary.accounts_processed += 1
                except Exception as e:
                    logger.error(
                        "Error processing account",
                        account_name=account["account_name"],
                        error=str(e),
                    )
                    summary.errors.append(f"{account['account_name']}: {str(e)}")

            # Take daily snapshots for this batch (outside the transaction)
            for account in batch:
                try:
                    await self.account_manager.take_daily_snapshot(
                        account["account_name"], today
                    )
                except Exception as e:
                    logger.error(
                        "Error taking snapshot",
                        account_name=account["account_name"],
                        error=str(e),
                    )

            logger.info(
                "Batch processed",
                batch_start=batch_start,
                batch_size=len(batch),
                accounts_processed=summary.accounts_processed,
            )

        logger.info(
            "Daily trade execution complete",
            accounts_processed=summary.accounts_processed,
            buys=summary.buys_executed,
            sells=summary.sells_executed,
            skipped_cash=summary.skipped_insufficient_cash,
            errors=len(summary.errors),
        )

        return summary

    async def _evaluate_account(
        self,
        account: dict,
        recommendations: dict[str, str],
        prices: dict[str, Decimal],
    ) -> dict:
        """Determine buy/sell actions for a single account based on recommendations.

        Args:
            account: Dict with account_id, account_name, cash_balance.
            recommendations: Dict mapping ticker -> recommendation (BUY/SELL/HOLD).
            prices: Dict mapping ticker -> latest closing price.

        Returns:
            Dict with counts: buys, sells, skipped_cash.
        """
        result = {"buys": 0, "sells": 0, "skipped_cash": 0}
        account_id = account["id"]
        account_name = account["account_name"]
        cash_balance = Decimal(str(account["cash_balance"]))

        # Get current holdings for this account
        holdings = {
            row["ticker"]: row for row in store.list_demo_holdings(account_id)
        }

        # Calculate current portfolio value for buy allocation
        holdings_value = sum(
            Decimal(str(row["quantity"])) * prices.get(row["ticker"], Decimal("0"))
            for row in holdings.values()
        )
        portfolio_value = cash_balance + holdings_value

        # Process each recommendation
        for ticker, recommendation in recommendations.items():
            if recommendation == "HOLD":
                # No action for HOLD
                continue

            close_price = prices.get(ticker)
            if close_price is None:
                logger.warning(
                    "No price available for ticker, skipping",
                    ticker=ticker,
                    account_name=account_name,
                )
                continue

            if recommendation == "SELL" and ticker in holdings:
                # Sell entire position
                txn = self._execute_sell(
                    account_id, account_name, ticker, close_price,
                    int(holdings[ticker]["quantity"]), cash_balance
                )
                if txn:
                    store.put_demo_transaction(account_id, txn)
                    store.delete_demo_holding(account_id, ticker)
                    cash_balance = txn["cash_after"]
                    store.update_demo_cash(account_id, cash_balance)
                    result["sells"] += 1

            elif recommendation == "BUY" and ticker not in holdings:
                # Buy shares (only if not already holding)
                txn = self._execute_buy(
                    account_id, account_name, ticker, close_price,
                    cash_balance, portfolio_value
                )
                if txn is None:
                    result["skipped_cash"] += 1
                else:
                    store.put_demo_transaction(account_id, txn)
                    store.upsert_demo_holding(
                        account_id, ticker, txn["quantity"], close_price
                    )
                    cash_balance = txn["cash_after"]
                    store.update_demo_cash(account_id, cash_balance)
                    result["buys"] += 1

        return result

    def _execute_buy(
        self,
        account_id: int,
        account_name: str,
        ticker: str,
        close_price: Decimal,
        cash_balance: Decimal,
        portfolio_value: Decimal,
    ) -> dict | None:
        """Execute a buy order: up to 10% of portfolio value, max whole shares.

        Allocates up to 10% of the account's total portfolio value for the
        purchase. Calculates max whole shares at closing price plus 1% commission.
        Skips if insufficient cash to buy even 1 share.

        Args:
            account_id: The account's database ID.
            account_name: The superhero name (for logging).
            ticker: Stock ticker to buy.
            close_price: Latest closing price.
            cash_balance: Current cash available.
            portfolio_value: Current total portfolio value.

        Returns:
            Transaction dict if executed, None if skipped.
        """
        # Allocate up to 10% of portfolio value
        max_allocation = (portfolio_value * MAX_BUY_ALLOCATION_PCT).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        # Don't exceed available cash
        budget = min(max_allocation, cash_balance)

        # Cost per share including commission: price * 1.01
        cost_per_share = close_price * (Decimal("1") + COMMISSION_RATE)

        # Check if we can afford at least 1 share
        if cost_per_share > budget:
            logger.info(
                "Insufficient cash for buy, skipping",
                account_name=account_name,
                ticker=ticker,
                cost_per_share=str(cost_per_share),
                budget=str(budget),
            )
            return None

        # Calculate max whole shares purchasable within budget
        max_shares = int(
            (budget / cost_per_share).to_integral_value(rounding=ROUND_DOWN)
        )
        if max_shares <= 0:
            logger.info(
                "Cannot afford any shares after calculation, skipping",
                account_name=account_name,
                ticker=ticker,
            )
            return None

        quantity = max_shares

        # Calculate transaction values
        total_value = (close_price * Decimal(quantity)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        commission_fee = (total_value * COMMISSION_RATE).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_cost = total_value + commission_fee
        cash_after = (cash_balance - total_cost).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        logger.info(
            "Executing buy",
            account_name=account_name,
            ticker=ticker,
            quantity=quantity,
            price=str(close_price),
            total_cost=str(total_cost),
            cash_after=str(cash_after),
        )

        return {
            "ticker": ticker,
            "action": "BUY",
            "quantity": quantity,
            "price_per_share": close_price,
            "total_value": total_value,
            "commission_fee": commission_fee,
            "cash_after": cash_after,
        }

    def _execute_sell(
        self,
        account_id: int,
        account_name: str,
        ticker: str,
        close_price: Decimal,
        quantity: int,
        cash_balance: Decimal,
    ) -> dict:
        """Execute a sell order: liquidate entire position.

        Sells all shares at the closing price, credits proceeds minus 1% commission.

        Args:
            account_id: The account's database ID.
            account_name: The superhero name (for logging).
            ticker: Stock ticker to sell.
            close_price: Latest closing price.
            quantity: Number of shares to sell (entire position).
            cash_balance: Current cash balance before sale.

        Returns:
            Transaction dict.
        """
        # Calculate transaction values
        total_value = (close_price * Decimal(quantity)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        commission_fee = (total_value * COMMISSION_RATE).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        proceeds = total_value - commission_fee
        cash_after = (cash_balance + proceeds).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        logger.info(
            "Executing sell",
            account_name=account_name,
            ticker=ticker,
            quantity=quantity,
            price=str(close_price),
            proceeds=str(proceeds),
            cash_after=str(cash_after),
        )

        return {
            "ticker": ticker,
            "action": "SELL",
            "quantity": quantity,
            "price_per_share": close_price,
            "total_value": total_value,
            "commission_fee": commission_fee,
            "cash_after": cash_after,
        }

    async def _get_latest_recommendations(self) -> dict[str, str]:
        """Fetch the latest AI recommendations from the analysis_results table.

        Returns:
            Dict mapping ticker -> recommendation (BUY, SELL, or HOLD).
        """
        recommendations = store.latest_recommendations()

        logger.info(
            "Fetched AI recommendations",
            count=len(recommendations),
            buys=sum(1 for v in recommendations.values() if v == "BUY"),
            sells=sum(1 for v in recommendations.values() if v == "SELL"),
            holds=sum(1 for v in recommendations.values() if v == "HOLD"),
        )

        return recommendations

    async def _get_latest_prices(self) -> dict[str, Decimal]:
        """Fetch the latest closing prices from the stock_data table.

        Returns:
            Dict mapping ticker -> latest closing price.
        """
        prices = store.latest_prices()
        logger.info("Fetched latest stock prices", count=len(prices))
        return prices

    async def _get_all_accounts(self) -> list[dict]:
        """Fetch all demo accounts from the database.

        Returns:
            List of dicts with id, account_name, cash_balance.
        """
        return store.list_demo_accounts()
