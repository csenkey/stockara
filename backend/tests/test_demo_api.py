"""Unit tests for the public demo trading account API endpoints."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.demo import router
from backend.src.models.demo_schemas import (
    DailySnapshot,
    DemoAccount,
    DemoHolding,
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


@pytest.fixture
def sample_leaderboard_entries():
    """Sample leaderboard data."""
    return [
        LeaderboardEntry(
            rank=1,
            account_name="Spider-Man",
            portfolio_value=Decimal("12500.00"),
            cash_balance=Decimal("3000.00"),
            gain_loss_pct=Decimal("25.00"),
            transaction_count=15,
            sparkline_data=[Decimal("10000"), Decimal("11000"), Decimal("12500")],
        ),
        LeaderboardEntry(
            rank=2,
            account_name="Batman",
            portfolio_value=Decimal("11000.00"),
            cash_balance=Decimal("2000.00"),
            gain_loss_pct=Decimal("10.00"),
            transaction_count=8,
            sparkline_data=[Decimal("10000"), Decimal("10500"), Decimal("11000")],
        ),
    ]


@pytest.fixture
def sample_account():
    """Sample demo account."""
    return DemoAccount(
        id=1,
        account_name="Spider-Man",
        cash_balance=Decimal("3000.00"),
        created_at=datetime(2025, 1, 1, 12, 0, 0),
    )


@pytest.fixture
def sample_transactions_response():
    """Sample paginated transactions."""
    return PaginatedTransactionsResponse(
        transactions=[
            DemoTransaction(
                id=1,
                ticker="AAPL",
                action="BUY",
                quantity=10,
                price_per_share=Decimal("150.00"),
                total_value=Decimal("1500.00"),
                commission_fee=Decimal("15.00"),
                cash_after=Decimal("8485.00"),
                executed_at=datetime(2025, 1, 15, 22, 30, 0),
            ),
        ],
        total=1,
        page=1,
        page_size=20,
        total_pages=1,
    )


@pytest.fixture
def sample_performance_data():
    """Sample performance time series."""
    return [
        DailySnapshot(
            snapshot_date=date(2025, 1, 1),
            portfolio_value=Decimal("10000.00"),
            cash_balance=Decimal("3000.00"),
            holdings_value=Decimal("7000.00"),
        ),
        DailySnapshot(
            snapshot_date=date(2025, 1, 2),
            portfolio_value=Decimal("10200.00"),
            cash_balance=Decimal("3000.00"),
            holdings_value=Decimal("7200.00"),
        ),
    ]


class TestLeaderboard:
    """Tests for GET /api/demo/leaderboard."""

    def test_returns_leaderboard(self, client, sample_leaderboard_entries):
        """Leaderboard endpoint returns ranked entries."""
        with patch(
            "backend.src.api.demo._manager.get_leaderboard",
            new_callable=AsyncMock,
            return_value=sample_leaderboard_entries,
        ):
            response = client.get("/api/demo/leaderboard")

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "last_updated" in data
        assert len(data["entries"]) == 2
        assert data["entries"][0]["account_name"] == "Spider-Man"
        assert data["entries"][0]["rank"] == 1

    def test_returns_empty_leaderboard(self, client):
        """Leaderboard returns empty list when no accounts exist."""
        with patch(
            "backend.src.api.demo._manager.get_leaderboard",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client.get("/api/demo/leaderboard")

        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []


class TestAccountDetail:
    """Tests for GET /api/demo/accounts/{name}."""

    def test_returns_account_detail(self, client, sample_account):
        """Account detail endpoint returns holdings and allocation."""
        holdings = [
            DemoHolding(
                ticker="AAPL",
                quantity=10,
                purchase_price=Decimal("150.00"),
                current_price=Decimal("160.00"),
                unrealized_gain_loss=Decimal("100.00"),
            ),
        ]

        with patch(
            "backend.src.api.demo._manager.get_account",
            new_callable=AsyncMock,
            return_value=sample_account,
        ), patch(
            "backend.src.api.demo._get_holdings_with_prices",
            new_callable=AsyncMock,
            return_value=holdings,
        ):
            response = client.get("/api/demo/accounts/Spider-Man")

        assert response.status_code == 200
        data = response.json()
        assert data["account_name"] == "Spider-Man"
        assert "portfolio_value" in data
        assert "holdings" in data
        assert "allocation" in data
        assert len(data["holdings"]) == 1
        assert data["holdings"][0]["ticker"] == "AAPL"

    def test_returns_404_for_nonexistent_account(self, client):
        """Returns 404 with descriptive message for non-existent account."""
        with patch(
            "backend.src.api.demo._manager.get_account",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get("/api/demo/accounts/NonExistent")

        assert response.status_code == 404
        data = response.json()
        assert "NonExistent" in data["detail"]


class TestTransactions:
    """Tests for GET /api/demo/accounts/{name}/transactions."""

    def test_returns_paginated_transactions(self, client, sample_transactions_response):
        """Transactions endpoint returns paginated results."""
        with patch(
            "backend.src.api.demo._manager.get_transactions",
            new_callable=AsyncMock,
            return_value=sample_transactions_response,
        ):
            response = client.get("/api/demo/accounts/Spider-Man/transactions")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert len(data["transactions"]) == 1

    def test_custom_pagination_params(self, client, sample_transactions_response):
        """Transactions endpoint respects custom page and page_size."""
        with patch(
            "backend.src.api.demo._manager.get_transactions",
            new_callable=AsyncMock,
            return_value=sample_transactions_response,
        ) as mock_get:
            response = client.get(
                "/api/demo/accounts/Spider-Man/transactions?page=2&page_size=10"
            )

        assert response.status_code == 200
        mock_get.assert_called_once_with("Spider-Man", page=2, page_size=10)

    def test_returns_404_for_nonexistent_account(self, client):
        """Returns 404 for non-existent account transactions."""
        with patch(
            "backend.src.api.demo._manager.get_transactions",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get("/api/demo/accounts/NonExistent/transactions")

        assert response.status_code == 404
        data = response.json()
        assert "NonExistent" in data["detail"]


class TestPerformance:
    """Tests for GET /api/demo/accounts/{name}/performance."""

    def test_returns_performance_series(self, client, sample_performance_data):
        """Performance endpoint returns daily time series."""
        with patch(
            "backend.src.api.demo._manager.get_performance_series",
            new_callable=AsyncMock,
            return_value=sample_performance_data,
        ):
            response = client.get("/api/demo/accounts/Spider-Man/performance")

        assert response.status_code == 200
        data = response.json()
        assert data["account_name"] == "Spider-Man"
        assert data["initial_value"] == "10000.00"
        assert len(data["data_points"]) == 2

    def test_returns_404_for_nonexistent_account(self, client):
        """Returns 404 for non-existent account performance."""
        with patch(
            "backend.src.api.demo._manager.get_performance_series",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get("/api/demo/accounts/NonExistent/performance")

        assert response.status_code == 404
        data = response.json()
        assert "NonExistent" in data["detail"]


class TestNoAuthRequired:
    """Verify all demo endpoints are accessible without authentication."""

    def test_leaderboard_no_auth(self, client):
        """Leaderboard accessible without any auth headers."""
        with patch(
            "backend.src.api.demo._manager.get_leaderboard",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client.get("/api/demo/leaderboard")
        assert response.status_code == 200

    def test_account_detail_no_auth(self, client, sample_account):
        """Account detail accessible without any auth headers."""
        with patch(
            "backend.src.api.demo._manager.get_account",
            new_callable=AsyncMock,
            return_value=sample_account,
        ), patch(
            "backend.src.api.demo._get_holdings_with_prices",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client.get("/api/demo/accounts/Spider-Man")
        assert response.status_code == 200
