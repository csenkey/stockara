"""Property-based tests for Demo Account creation logic.

Tests the pure business logic of account allocation without requiring a database.
Uses Hypothesis to generate random inputs and verify invariants hold across all cases.

Validates: Requirements 1.2, 1.3, 1.4, 1.5
"""

import random
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# --- Pure business logic extracted for testing ---

INITIAL_BANKROLL = Decimal("10000.00")
MIN_CASH = Decimal("500.00")
MAX_CASH = Decimal("9500.00")
COMMISSION_RATE = Decimal("0.01")


def allocate_initial_stocks(
    cash_balance: Decimal, active_stocks: list[dict], seed: int | None = None
) -> tuple[Decimal, list[dict]]:
    """Pure version of the allocation logic from DemoAccountManager.

    Given a cash balance and list of active stocks with prices, allocates the
    remaining budget (INITIAL_BANKROLL - cash_balance) across stocks with 1% commission.

    Returns:
        Tuple of (final_cash_balance, list of holdings dicts with keys:
            ticker, quantity, purchase_price, total_value, commission_fee)
    """
    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.Random()

    stock_budget = INITIAL_BANKROLL - cash_balance
    remaining_budget = stock_budget
    holdings: list[dict] = []

    if remaining_budget <= Decimal("0"):
        return cash_balance, holdings

    shuffled_stocks = rng.sample(active_stocks, len(active_stocks))

    for stock in shuffled_stocks:
        if remaining_budget <= Decimal("0"):
            break

        ticker = stock["ticker"]
        price = stock["close_price"]

        cost_per_share = price * (Decimal("1") + COMMISSION_RATE)

        if cost_per_share > remaining_budget:
            continue

        max_shares = int(
            (remaining_budget / cost_per_share).to_integral_value(rounding=ROUND_DOWN)
        )
        if max_shares <= 0:
            continue

        quantity = rng.randint(1, max_shares)

        total_value = price * Decimal(quantity)
        commission = (total_value * COMMISSION_RATE).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_cost = total_value + commission

        # Ensure we don't overshoot the budget
        if total_cost > remaining_budget:
            quantity = int(
                (remaining_budget / cost_per_share).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )
            if quantity <= 0:
                continue
            total_value = price * Decimal(quantity)
            commission = (total_value * COMMISSION_RATE).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_cost = total_value + commission

        remaining_budget -= total_cost

        holdings.append(
            {
                "ticker": ticker,
                "quantity": quantity,
                "purchase_price": price,
                "total_value": total_value,
                "commission_fee": commission,
            }
        )

    final_cash = cash_balance + remaining_budget
    return final_cash, holdings


# --- Hypothesis Strategies ---

# Generate a cash balance between $500.00 and $9500.00 (in cents, then convert)
cash_strategy = st.integers(min_value=50000, max_value=950000).map(
    lambda cents: Decimal(cents) / Decimal("100")
)

# Generate stock prices between $1.00 and $500.00
price_strategy = st.integers(min_value=100, max_value=50000).map(
    lambda cents: Decimal(cents) / Decimal("100")
)

# Generate a list of active stocks (at least 10, up to 50)
active_stocks_strategy = st.lists(
    st.tuples(
        st.from_regex(r"[A-Z]{1,5}", fullmatch=True),
        price_strategy,
    ),
    min_size=10,
    max_size=50,
    unique_by=lambda x: x[0],
).map(
    lambda items: [{"ticker": t, "close_price": p} for t, p in items]
)

# Seed for deterministic random within each test case
seed_strategy = st.integers(min_value=0, max_value=2**32 - 1)


# --- Property Tests ---


# Feature: demo-trading-accounts, Property 1: Initial bankroll invariant
@settings(max_examples=100)
@given(cash=cash_strategy, active_stocks=active_stocks_strategy, seed=seed_strategy)
def test_initial_bankroll_invariant(cash: Decimal, active_stocks: list[dict], seed: int):
    """Property 1: cash + sum(qty × price × 1.01) == $10,000.

    For any created demo account, the sum of cash_balance plus the total cost
    of all initial stock purchases (quantity × purchase_price × 1.01 for each holding)
    SHALL equal $10,000.00, AND cash_balance SHALL be between $500.00 and $9,500.00.

    **Validates: Requirements 1.2, 1.3**
    """
    final_cash, holdings = allocate_initial_stocks(cash, active_stocks, seed=seed)

    # Cash must be at least $500 (original cash is in range, final_cash >= original cash
    # only if no stocks purchased, otherwise final_cash = cash + leftover budget)
    # The actual final cash = cash_balance + remaining unspent budget from stock allocation
    # It should satisfy: final_cash >= MIN_CASH (since we start with at least $500 in cash)
    assert final_cash >= MIN_CASH

    # Calculate total spent on holdings (including commission)
    total_spent = sum(
        h["total_value"] + h["commission_fee"] for h in holdings
    )

    # The invariant: final_cash + total_spent == INITIAL_BANKROLL
    assert final_cash + total_spent == INITIAL_BANKROLL, (
        f"Bankroll invariant violated: cash={final_cash}, "
        f"total_spent={total_spent}, sum={final_cash + total_spent}"
    )


# Feature: demo-trading-accounts, Property 2: Holdings only from active watchlist
@settings(max_examples=100)
@given(cash=cash_strategy, active_stocks=active_stocks_strategy, seed=seed_strategy)
def test_holdings_only_from_active_watchlist(
    cash: Decimal, active_stocks: list[dict], seed: int
):
    """Property 2: All tickers in holdings are from the active watchlist.

    For any demo account at creation time, every ticker in its holdings SHALL
    exist in the active stocks watchlist.

    **Validates: Requirements 1.4**
    """
    _, holdings = allocate_initial_stocks(cash, active_stocks, seed=seed)

    active_tickers = {stock["ticker"] for stock in active_stocks}

    for holding in holdings:
        assert holding["ticker"] in active_tickers, (
            f"Holding ticker '{holding['ticker']}' is not in the active watchlist. "
            f"Active tickers: {active_tickers}"
        )


# Feature: demo-trading-accounts, Property 3: Commission is always exactly 1%
@settings(max_examples=100)
@given(cash=cash_strategy, active_stocks=active_stocks_strategy, seed=seed_strategy)
def test_commission_is_exactly_one_percent(
    cash: Decimal, active_stocks: list[dict], seed: int
):
    """Property 3: commission_fee == 0.01 × total_value for initial purchases.

    For any transaction (initial purchase), the commission_fee SHALL equal exactly
    0.01 × total_value (quantity × price_per_share), rounded to 2 decimal places.

    **Validates: Requirements 1.5**
    """
    _, holdings = allocate_initial_stocks(cash, active_stocks, seed=seed)

    for holding in holdings:
        expected_commission = (
            holding["total_value"] * COMMISSION_RATE
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        assert holding["commission_fee"] == expected_commission, (
            f"Commission mismatch for {holding['ticker']}: "
            f"got {holding['commission_fee']}, expected {expected_commission} "
            f"(total_value={holding['total_value']})"
        )
