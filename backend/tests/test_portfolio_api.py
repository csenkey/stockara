"""Unit tests for the portfolio management API."""

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.src.api.portfolio import router
from backend.src.services.encryption_service import DecryptionError


@pytest.fixture
def app():
    """Create a FastAPI app with the portfolio router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def user_id():
    """Generate a test user UUID."""
    return str(uuid4())


@pytest.fixture
def mock_db_connection():
    """Mock the database connection context manager."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    class MockAsyncContextManager:
        def __init__(self):
            self.conn = mock_conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *args):
            pass

    with patch(
        "backend.src.api.portfolio.get_db_connection",
        return_value=MockAsyncContextManager(),
    ):
        yield mock_conn, mock_cursor


@pytest.fixture
def mock_encryption_service():
    """Mock the encryption service."""
    mock_service = MagicMock()
    with patch(
        "backend.src.api.portfolio._get_encryption_service",
        return_value=mock_service,
    ):
        yield mock_service


class TestGetPortfolio:
    """Tests for GET /api/portfolio."""

    def test_returns_empty_portfolio_when_none_exists(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None

        response = client.get("/api/portfolio", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        data = response.json()
        assert data["holdings"] == []
        assert data["updated_at"] is None

    def test_returns_decrypted_portfolio(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        from datetime import datetime

        mock_cursor.fetchone.return_value = {
            "encrypted_data": "encrypted_blob",
            "updated_at": datetime(2025, 1, 15, 10, 0, 0),
        }
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [
                {"ticker": "AAPL", "quantity": 50, "buying_price": 175.20, "added_date": "2025-03-15"}
            ]
        }

        response = client.get("/api/portfolio", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        data = response.json()
        assert len(data["holdings"]) == 1
        assert data["holdings"][0]["ticker"] == "AAPL"
        mock_encryption_service.decrypt_portfolio.assert_called_once_with("encrypted_blob")

    def test_returns_500_on_decryption_failure(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        from datetime import datetime

        mock_cursor.fetchone.return_value = {
            "encrypted_data": "bad_data",
            "updated_at": datetime(2025, 1, 15),
        }
        mock_encryption_service.decrypt_portfolio.side_effect = DecryptionError(
            "Failed to decrypt"
        )

        response = client.get("/api/portfolio", headers={"X-User-Id": user_id})

        assert response.status_code == 500
        assert "Failed to retrieve portfolio data" in response.json()["detail"]

    def test_returns_401_without_auth_header(self, client):
        response = client.get("/api/portfolio")

        assert response.status_code == 401

    def test_returns_401_with_invalid_uuid(self, client):
        response = client.get("/api/portfolio", headers={"X-User-Id": "not-a-uuid"})

        assert response.status_code == 401


class TestAddStockToPortfolio:
    """Tests for PUT /api/portfolio/stocks."""

    def test_adds_stock_to_empty_portfolio(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        from datetime import datetime

        # First call: ticker exists check; Second call: portfolio lookup; Third: upsert
        mock_cursor.fetchone.side_effect = [
            {"ticker": "AAPL"},  # ticker exists in watchlist
            None,  # no existing portfolio
            {"updated_at": datetime(2025, 1, 15, 10, 0, 0)},  # upsert result
        ]
        mock_encryption_service.encrypt_portfolio.return_value = "new_encrypted"

        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": 50, "buying_price": 175.20},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["holdings"]) == 1
        assert data["holdings"][0]["ticker"] == "AAPL"
        assert data["holdings"][0]["quantity"] == 50

    def test_adds_stock_to_existing_portfolio(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        from datetime import datetime

        mock_cursor.fetchone.side_effect = [
            {"ticker": "MSFT"},  # ticker exists
            {"encrypted_data": "existing_encrypted"},  # existing portfolio
            {"updated_at": datetime(2025, 1, 15, 10, 0, 0)},  # upsert
        ]
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [
                {"ticker": "AAPL", "quantity": 50, "buying_price": 175.20, "added_date": "2025-03-15"}
            ]
        }
        mock_encryption_service.encrypt_portfolio.return_value = "updated_encrypted"

        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "MSFT", "quantity": 30, "buying_price": 420.00},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["holdings"]) == 2

    def test_rejects_ticker_not_in_watchlist(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None  # ticker not found

        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "FAKE", "quantity": 10, "buying_price": 50.00},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 400
        assert "does not exist in the watchlist" in response.json()["detail"]

    def test_rejects_zero_quantity(self, client, user_id):
        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": 0, "buying_price": 175.20},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 422

    def test_rejects_negative_quantity(self, client, user_id):
        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": -5, "buying_price": 175.20},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 422

    def test_rejects_zero_buying_price(self, client, user_id):
        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": 10, "buying_price": 0},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 422

    def test_rejects_negative_buying_price(self, client, user_id):
        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": 10, "buying_price": -50.0},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 422

    def test_returns_401_without_auth(self, client):
        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": 10, "buying_price": 175.20},
        )

        assert response.status_code == 401


class TestRemoveStockFromPortfolio:
    """Tests for DELETE /api/portfolio/stocks/{ticker}."""

    def test_removes_stock_from_portfolio(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {"encrypted_data": "encrypted_blob"}
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [
                {"ticker": "AAPL", "quantity": 50, "buying_price": 175.20, "added_date": "2025-03-15"},
                {"ticker": "MSFT", "quantity": 30, "buying_price": 420.00, "added_date": "2025-04-01"},
            ]
        }
        mock_encryption_service.encrypt_portfolio.return_value = "updated_encrypted"

        response = client.delete(
            "/api/portfolio/stocks/AAPL",
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 200
        assert "removed" in response.json()["message"]

    def test_returns_404_when_portfolio_not_found(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None

        response = client.delete(
            "/api/portfolio/stocks/AAPL",
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 404
        assert "Portfolio not found" in response.json()["detail"]

    def test_returns_404_when_stock_not_in_portfolio(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {"encrypted_data": "encrypted_blob"}
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [
                {"ticker": "MSFT", "quantity": 30, "buying_price": 420.00, "added_date": "2025-04-01"}
            ]
        }

        response = client.delete(
            "/api/portfolio/stocks/AAPL",
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 404
        assert "not found in portfolio" in response.json()["detail"]

    def test_returns_500_on_decryption_failure(
        self, client, user_id, mock_db_connection, mock_encryption_service
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {"encrypted_data": "bad_data"}
        mock_encryption_service.decrypt_portfolio.side_effect = DecryptionError(
            "Failed to decrypt"
        )

        response = client.delete(
            "/api/portfolio/stocks/AAPL",
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 500

    def test_returns_401_without_auth(self, client):
        response = client.delete("/api/portfolio/stocks/AAPL")

        assert response.status_code == 401
