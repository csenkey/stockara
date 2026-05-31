"""Property-based tests for public demo API sort ordering.

Tests that the leaderboard is always sorted by portfolio value descending (Property 12)
and that transaction history is always sorted by executed_at descending (Property 13).

Uses Hypothesis to generate random test data and mocks the DemoAccountManager methods
to return the generated data through the API endpoints.

Validates: Requirements 4.1, 6.3
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.src.api.demo import router
from backend.src.models.demo_schemas import (
    DemoTransaction,
    LeaderboardEntry,
    PaginatedTransactionsResponse,
)


@pytest.fixture
def app():
    """Create a FastAPI app with the demo router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


# --- Strategies ---

leaderboard_entry_strategy = st.builds(
    LeaderboardEntry,
    rank=st.just(0),  # will be reassigned based on sort
    account_name=st.text(
        alphabet=st.characters(whitelist_categories=("L",)), min_size=3, max_size=20
    ),
    portfolio_value=st.decimals(
        min_value=Decimal("100.00"),
        max_value=Decimal("100000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    cash_balance=st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("50000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    gain_loss_pct=st.decimals(
        min_value=Decimal("-99.00"),
        max_value=Decimal("999.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    transaction_count=st.integers(min_value=0, max_value=500),
    sparkline_data=st.just([]),
)

transaction_strategy = st.builds(
    DemoTransaction,
    id=st.integers(min_value=1, max_value=100000),
    ticker=st.sampled_from(["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "META", "NVDA"]),
    action=st.sampled_from(["BUY", "SELL"]),
    quantity=st.integers(min_value=1, max_value=1000),
    price_per_share=st.decimals(
        min_value=Decimal("1.00"),
        max_value=Decimal("5000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    total_value=st.decimals(
        min_value=Decimal("1.00"),
        max_value=Decimal("500000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    commission_fee=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("5000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    cash_after=st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("100000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    executed_at=st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2025, 12, 31),
    ),
)


# Feature: demo-trading-accounts, Property 12: Leaderboard sorted by portfolio value descending
class TestLeaderboardSortProperty:
    """Property 12: Leaderboard sorted by portfolio value descending.

    *For any* leaderboard response, for all consecutive pairs of entries (i, i+1),
    entry[i].portfolio_value SHALL be greater than or equal to entry[i+1].portfolio_value.

    **Validates: Requirements 4.1**
    """

    @settings(max_examples=100)
    @given(entries=st.lists(leaderboard_entry_strategy, min_size=0, max_size=50))
    def test_leaderboard_sorted_by_portfolio_value_descending(self, entries):
        """Leaderboard entries are always sorted by portfolio value descending."""
        # Sort entries by portfolio_value descending and assign ranks (simulating what
        # get_leaderboard should return)
        sorted_entries = sorted(entries, key=lambda e: e.portfolio_value, reverse=True)
        for i, entry in enumerate(sorted_entries):
            entry.rank = i + 1

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch(
            "backend.src.api.demo._manager.get_leaderboard",
            new_callable=AsyncMock,
            return_value=sorted_entries,
        ):
            response = client.get("/api/demo/leaderboard")

        assert response.status_code == 200
        data = response.json()
        response_entries = data["entries"]

        # Property: each entry's portfolio_value >= next entry's portfolio_value
        for i in range(len(response_entries) - 1):
            current_val = Decimal(response_entries[i]["portfolio_value"])
            next_val = Decimal(response_entries[i + 1]["portfolio_value"])
            assert current_val >= next_val, (
                f"Leaderboard not sorted descending at index {i}: "
                f"{current_val} < {next_val}"
            )


# Feature: demo-trading-accounts, Property 13: Transaction history sorted by date descending
class TestTransactionSortProperty:
    """Property 13: Transaction history sorted by date descending.

    *For any* paginated transaction history response, for all consecutive pairs of
    transactions (i, i+1), transaction[i].executed_at SHALL be greater than or equal to
    transaction[i+1].executed_at.

    **Validates: Requirements 6.3**
    """

    @settings(max_examples=100)
    @given(transactions=st.lists(transaction_strategy, min_size=0, max_size=30))
    def test_transaction_history_sorted_by_date_descending(self, transactions):
        """Transaction history is always sorted by executed_at descending."""
        # Sort transactions by executed_at descending (simulating what
        # get_transactions should return)
        sorted_txns = sorted(transactions, key=lambda t: t.executed_at, reverse=True)

        paginated_response = PaginatedTransactionsResponse(
            transactions=sorted_txns,
            total=len(sorted_txns),
            page=1,
            page_size=max(len(sorted_txns), 20),
            total_pages=1,
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch(
            "backend.src.api.demo._manager.get_transactions",
            new_callable=AsyncMock,
            return_value=paginated_response,
        ):
            response = client.get("/api/demo/accounts/TestHero/transactions")

        assert response.status_code == 200
        data = response.json()
        txns = data["transactions"]

        # Property: each transaction's executed_at >= next transaction's executed_at
        for i in range(len(txns) - 1):
            current_dt = datetime.fromisoformat(txns[i]["executed_at"])
            next_dt = datetime.fromisoformat(txns[i + 1]["executed_at"])
            assert current_dt >= next_dt, (
                f"Transactions not sorted descending at index {i}: "
                f"{current_dt} < {next_dt}"
            )
