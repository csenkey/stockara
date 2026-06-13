"""Property-based tests for Demo Trading persistence logic.

Tests that daily snapshots are generated for all accounts when transactions occur,
ensuring data completeness for the portfolio time-series feature.

Validates: Requirements 3.3
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st


# --- Hypothesis Strategies ---

# Number of accounts between 1 and 100
num_accounts_strategy = st.integers(min_value=1, max_value=100)

# Snapshot date
snapshot_date_strategy = st.dates(
    min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)
)

# Cash balance between $100.00 and $50,000.00
cash_strategy = st.integers(min_value=10000, max_value=5000000).map(
    lambda cents: Decimal(cents) / Decimal("100")
)

# Number of tickers with recommendations (at least 1 for a transaction to occur)
num_tickers_strategy = st.integers(min_value=1, max_value=20)

# Stock price
price_strategy = st.integers(min_value=100, max_value=100000).map(
    lambda cents: Decimal(cents) / Decimal("100")
)


def build_accounts(num_accounts, cash_balance):
    """Build a list of mock account dicts."""
    return [
        {
            "id": i + 1,
            "account_name": f"Hero-{i+1}",
            "cash_balance": cash_balance,
        }
        for i in range(num_accounts)
    ]


def build_recommendations(num_tickers):
    """Build recommendations with at least one BUY to ensure transactions happen."""
    recs = {}
    for i in range(num_tickers):
        ticker = f"TKR{i}"
        # First ticker is always BUY to guarantee at least one transaction
        recs[ticker] = "BUY" if i == 0 else "HOLD"
    return recs


def build_prices(recommendations):
    """Build price dict for all tickers in recommendations."""
    return {ticker: Decimal("50.00") for ticker in recommendations}


# Feature: demo-trading-accounts, Property 11: Daily snapshot exists for each trading day
@settings(max_examples=100)
@given(
    num_accounts=num_accounts_strategy,
    cash_balance=cash_strategy,
    num_tickers=num_tickers_strategy,
    trading_date=snapshot_date_strategy,
)
def test_daily_snapshot_exists_for_each_trading_day(
    num_accounts: int,
    cash_balance: Decimal,
    num_tickers: int,
    trading_date: date,
):
    """Property 11: Daily snapshot exists for each trading day.

    If any transaction is executed on a day, all accounts have a snapshot.

    For any day where at least one transaction was executed, a daily snapshot
    SHALL exist for every demo account, containing portfolio_value, cash_balance,
    and holdings_value.

    We verify this by testing the contract of execute_daily_trades:
    given a list of accounts and a trading day with transactions, snapshots
    are taken for ALL accounts in the batch (not just accounts that traded).

    **Validates: Requirements 3.3**
    """
    import asyncio

    # Ensure cash is enough to buy at least 1 share (price $50 * 1.01 = $50.50)
    assume(cash_balance >= Decimal("51.00"))

    accounts = build_accounts(num_accounts, cash_balance)
    recommendations = build_recommendations(num_tickers)
    prices = build_prices(recommendations)

    # Track which accounts had snapshots taken
    snapshot_calls = []

    async def mock_take_daily_snapshot(account_name: str, snapshot_date: date):
        snapshot_calls.append({
            "account_name": account_name,
            "snapshot_date": snapshot_date,
        })

    # We patch the executor's internals to avoid DB access
    with patch(
        "backend.src.services.demo_trade_executor.DemoTradeExecutor._get_latest_recommendations",
        new_callable=AsyncMock,
        return_value=recommendations,
    ), patch(
        "backend.src.services.demo_trade_executor.DemoTradeExecutor._get_latest_prices",
        new_callable=AsyncMock,
        return_value=prices,
    ), patch(
        "backend.src.services.demo_trade_executor.DemoTradeExecutor._get_all_accounts",
        new_callable=AsyncMock,
        return_value=accounts,
    ), patch(
        "backend.src.services.demo_trade_executor.DemoTradeExecutor._evaluate_account",
        new_callable=AsyncMock,
        return_value={"buys": 1, "sells": 0, "skipped_cash": 0},
    ), patch(
        "backend.src.services.demo_account_manager.DemoAccountManager.take_daily_snapshot",
        side_effect=mock_take_daily_snapshot,
    ), patch(
        "backend.src.services.demo_trade_executor.date",
    ) as mock_date:
        # Mock date.today() to return our generated trading_date
        mock_date.today.return_value = trading_date

        from backend.src.services.demo_trade_executor import DemoTradeExecutor

        executor = DemoTradeExecutor()

        # Run the daily trades
        asyncio.run(executor.execute_daily_trades())

    # PROPERTY ASSERTION:
    # Every account must have a snapshot taken on the trading day
    snapshot_account_names = {s["account_name"] for s in snapshot_calls}
    expected_account_names = {a["account_name"] for a in accounts}

    assert snapshot_account_names == expected_account_names, (
        f"Snapshots missing for accounts: {expected_account_names - snapshot_account_names}. "
        f"Expected snapshots for all {num_accounts} accounts, got {len(snapshot_account_names)}."
    )

    # Every snapshot must be for the correct trading date
    for snapshot in snapshot_calls:
        assert snapshot["snapshot_date"] == trading_date, (
            f"Snapshot date mismatch: expected {trading_date}, got {snapshot['snapshot_date']}"
        )

    # Verify completeness: exactly one snapshot per account
    assert len(snapshot_calls) == num_accounts, (
        f"Expected exactly {num_accounts} snapshots, got {len(snapshot_calls)}"
    )


# --- Additional Strategies for Property 10 ---

ticker_strategy = st.from_regex(r"[A-Z]{1,5}", fullmatch=True)

action_strategy = st.sampled_from(["BUY", "SELL"])

# Quantity between 1 and 10000
quantity_strategy = st.integers(min_value=1, max_value=10000)

# Price per share between $0.01 and $9999.99 (in cents then convert)
txn_price_strategy = st.integers(min_value=1, max_value=999999).map(
    lambda cents: Decimal(cents) / Decimal("100")
)

# Account ID
account_id_strategy = st.integers(min_value=1, max_value=100000)


@st.composite
def transaction_strategy(draw):
    """Generate a complete transaction dict with consistent values."""
    from decimal import ROUND_HALF_UP

    ticker = draw(ticker_strategy)
    action = draw(action_strategy)
    quantity = draw(quantity_strategy)
    price_per_share = draw(txn_price_strategy)

    total_value = (price_per_share * Decimal(quantity)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    commission_fee = (total_value * Decimal("0.01")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    # cash_after is some arbitrary valid cash balance
    cash_after = draw(
        st.integers(min_value=0, max_value=10000000).map(
            lambda cents: Decimal(cents) / Decimal("100")
        )
    )

    return {
        "ticker": ticker,
        "action": action,
        "quantity": quantity,
        "price_per_share": price_per_share,
        "total_value": total_value,
        "commission_fee": commission_fee,
        "cash_after": cash_after,
    }


# Feature: demo-trading-accounts, Property 10: Transaction persistence round-trip
@settings(max_examples=100)
@given(
    account_id=account_id_strategy,
    txn=transaction_strategy(),
)
@pytest.mark.asyncio
async def test_transaction_persistence_round_trip(account_id: int, txn: dict):
    """Property 10: Transaction persistence round-trip.

    For any transaction recorded via DemoAccountManager.record_transaction(),
    when queried back via get_transactions(), all fields should match exactly
    (ticker, action, quantity, price_per_share, total_value, commission_fee, cash_after).

    **Validates: Requirements 2.8, 3.1, 3.2**
    """
    from backend.src.services.demo_account_manager import DemoAccountManager

    stored_transactions: list[dict] = []
    account_name = "TestHero"

    manager = DemoAccountManager()

    def put_demo_transaction(record_account_id, record_txn):
        stored_transactions.append({
            "id": "txn-1",
            "account_id": record_account_id,
            "ticker": record_txn["ticker"],
            "action": record_txn["action"],
            "quantity": record_txn["quantity"],
            "price_per_share": record_txn["price_per_share"],
            "total_value": record_txn["total_value"],
            "commission_fee": record_txn["commission_fee"],
            "cash_after": record_txn["cash_after"],
            "executed_at": datetime(2024, 1, 15, 12, 0, 0),
        })

    with patch("backend.src.services.demo_account_manager.store") as mock_store:
        mock_store.put_demo_transaction.side_effect = put_demo_transaction
        mock_store.get_demo_account_by_name.return_value = {
            "id": account_id,
            "account_name": account_name,
            "cash_balance": Decimal("10000.00"),
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
        }
        mock_store.list_demo_transactions.side_effect = lambda queried_account_id: [
            t for t in stored_transactions if t["account_id"] == queried_account_id
        ]

        # Phase 1: Record the transaction
        await manager.record_transaction(account_id, txn)

        # Phase 2: Query the transaction back
        result = await manager.get_transactions(account_name, page=1, page_size=100)

    # Phase 3: Verify round-trip — all fields must match
    assert result is not None, "get_transactions should return a result"
    assert len(result.transactions) == 1, (
        f"Expected 1 transaction, got {len(result.transactions)}"
    )

    retrieved = result.transactions[0]

    assert retrieved.ticker == txn["ticker"], (
        f"Ticker mismatch: wrote {txn['ticker']!r}, read {retrieved.ticker!r}"
    )
    assert retrieved.action == txn["action"], (
        f"Action mismatch: wrote {txn['action']!r}, read {retrieved.action!r}"
    )
    assert retrieved.quantity == txn["quantity"], (
        f"Quantity mismatch: wrote {txn['quantity']}, read {retrieved.quantity}"
    )
    assert retrieved.price_per_share == txn["price_per_share"], (
        f"Price mismatch: wrote {txn['price_per_share']}, read {retrieved.price_per_share}"
    )
    assert retrieved.total_value == txn["total_value"], (
        f"Total value mismatch: wrote {txn['total_value']}, read {retrieved.total_value}"
    )
    assert retrieved.commission_fee == txn["commission_fee"], (
        f"Commission mismatch: wrote {txn['commission_fee']}, read {retrieved.commission_fee}"
    )
    assert retrieved.cash_after == txn["cash_after"], (
        f"Cash after mismatch: wrote {txn['cash_after']}, read {retrieved.cash_after}"
    )
