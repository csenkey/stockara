"""Unit tests for the stock watchlist CRUD API."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.src.api.stocks import router


@pytest.fixture
def app():
    """Create a FastAPI app with the stocks router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_db_connection():
    """Mock the database connection context manager."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    async def mock_get_connection():
        return mock_conn

    class MockAsyncContextManager:
        def __init__(self):
            self.conn = mock_conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *args):
            pass

    with patch("backend.src.api.stocks.get_db_connection", return_value=MockAsyncContextManager()):
        yield mock_conn, mock_cursor


class TestListStocks:
    """Tests for GET /api/stocks."""

    def test_list_stocks_empty(self, client, mock_db_connection):
        """Test listing stocks when none exist."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = []

        response = client.get("/api/stocks")
        assert response.status_code == 200
        data = response.json()
        assert data["stocks"] == []
        assert data["total"] == 0

    def test_list_stocks_with_results(self, client, mock_db_connection):
        """Test listing stocks with results."""
        _, mock_cursor = mock_db_connection
        from datetime import datetime

        mock_cursor.fetchall.return_value = [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
                "company_size": "blue_chip",
                "added_at": datetime(2025, 1, 1, 0, 0, 0),
                "is_active": True,
            }
        ]

        response = client.get("/api/stocks")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["stocks"][0]["ticker"] == "AAPL"
        assert data["stocks"][0]["sector"] == "Technology"

    def test_list_stocks_filter_by_sector(self, client, mock_db_connection):
        """Test filtering stocks by sector."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = []

        response = client.get("/api/stocks?sector=Technology")
        assert response.status_code == 200

    def test_list_stocks_filter_by_company_size(self, client, mock_db_connection):
        """Test filtering stocks by company size."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = []

        response = client.get("/api/stocks?company_size=blue_chip")
        assert response.status_code == 200

    def test_list_stocks_invalid_sector(self, client, mock_db_connection):
        """Test filtering with invalid sector returns 400."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = []

        response = client.get("/api/stocks?sector=InvalidSector")
        assert response.status_code == 400


class TestAddStock:
    """Tests for POST /api/stocks."""

    def test_add_stock_success(self, client, mock_db_connection):
        """Test successfully adding a stock."""
        _, mock_cursor = mock_db_connection
        from datetime import datetime

        mock_cursor.fetchone.side_effect = [
            None,  # First call: check if exists
            {  # Second call: INSERT RETURNING
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
                "company_size": "blue_chip",
                "added_at": datetime(2025, 1, 1, 0, 0, 0),
                "is_active": True,
            },
        ]

        response = client.post(
            "/api/stocks",
            json={
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
                "company_size": "blue_chip",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["ticker"] == "AAPL"
        assert data["company_name"] == "Apple Inc"

    def test_add_stock_duplicate(self, client, mock_db_connection):
        """Test adding a duplicate stock returns 409."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {"ticker": "AAPL"}

        response = client.post(
            "/api/stocks",
            json={
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
                "company_size": "blue_chip",
            },
        )
        assert response.status_code == 409

    def test_add_stock_missing_sector(self, client, mock_db_connection):
        """Test adding stock without sector returns 422."""
        response = client.post(
            "/api/stocks",
            json={
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "company_size": "blue_chip",
            },
        )
        assert response.status_code == 422

    def test_add_stock_missing_company_size(self, client, mock_db_connection):
        """Test adding stock without company_size returns 422."""
        response = client.post(
            "/api/stocks",
            json={
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
            },
        )
        assert response.status_code == 422

    def test_add_stock_invalid_sector(self, client, mock_db_connection):
        """Test adding stock with invalid sector returns 422."""
        response = client.post(
            "/api/stocks",
            json={
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "InvalidSector",
                "company_size": "blue_chip",
            },
        )
        assert response.status_code == 422

    def test_add_stock_invalid_company_size(self, client, mock_db_connection):
        """Test adding stock with invalid company_size returns 422."""
        response = client.post(
            "/api/stocks",
            json={
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
                "company_size": "mega_corp",
            },
        )
        assert response.status_code == 422


class TestRemoveStock:
    """Tests for DELETE /api/stocks/{ticker}."""

    def test_remove_stock_success(self, client, mock_db_connection):
        """Test successfully removing a stock."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {"ticker": "AAPL"}

        response = client.delete("/api/stocks/AAPL")
        assert response.status_code == 200
        assert "removed" in response.json()["message"]

    def test_remove_stock_not_found(self, client, mock_db_connection):
        """Test removing a non-existent stock returns 404."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None

        response = client.delete("/api/stocks/FAKE")
        assert response.status_code == 404


class TestUpdateStock:
    """Tests for PUT /api/stocks/{ticker}."""

    def test_update_stock_success(self, client, mock_db_connection):
        """Test successfully updating a stock."""
        _, mock_cursor = mock_db_connection
        from datetime import datetime

        mock_cursor.fetchone.side_effect = [
            {"ticker": "AAPL"},  # First call: check exists
            {  # Second call: UPDATE RETURNING
                "ticker": "AAPL",
                "company_name": "Apple Inc Updated",
                "sector": "Technology",
                "company_size": "blue_chip",
                "added_at": datetime(2025, 1, 1, 0, 0, 0),
                "is_active": True,
            },
        ]

        response = client.put(
            "/api/stocks/AAPL",
            json={"company_name": "Apple Inc Updated"},
        )
        assert response.status_code == 200
        assert response.json()["company_name"] == "Apple Inc Updated"

    def test_update_stock_not_found(self, client, mock_db_connection):
        """Test updating a non-existent stock returns 404."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None

        response = client.put(
            "/api/stocks/FAKE",
            json={"company_name": "Updated"},
        )
        assert response.status_code == 404

    def test_update_stock_no_fields(self, client, mock_db_connection):
        """Test updating with no fields returns 400."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {"ticker": "AAPL"}

        response = client.put("/api/stocks/AAPL", json={})
        assert response.status_code == 400

    def test_update_stock_invalid_sector(self, client, mock_db_connection):
        """Test updating with invalid sector returns 422."""
        response = client.put(
            "/api/stocks/AAPL",
            json={"sector": "InvalidSector"},
        )
        assert response.status_code == 422

    def test_update_stock_invalid_company_size(self, client, mock_db_connection):
        """Test updating with invalid company_size returns 422."""
        response = client.put(
            "/api/stocks/AAPL",
            json={"company_size": "mega_corp"},
        )
        assert response.status_code == 422
