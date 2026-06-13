"""Unit tests for the portfolio management API."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.portfolio import router
from backend.src.services.encryption_service import DecryptionError


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def user_id():
    return str(uuid4())


@pytest.fixture
def mock_store():
    with patch("backend.src.api.portfolio.store") as store:
        yield store


@pytest.fixture
def mock_encryption_service():
    mock_service = MagicMock()
    with patch("backend.src.api.portfolio._get_encryption_service", return_value=mock_service):
        yield mock_service


class TestGetPortfolio:
    def test_returns_empty_portfolio_when_none_exists(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_portfolio.return_value = None

        response = client.get("/api/portfolio", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        assert response.json() == {"holdings": [], "updated_at": None}

    def test_returns_decrypted_portfolio(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_portfolio.return_value = {
            "encrypted_data": "encrypted_blob",
            "updated_at": "2025-01-15T10:00:00",
        }
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [
                {
                    "ticker": "AAPL",
                    "quantity": 50,
                    "buying_price": 175.20,
                    "added_date": "2025-03-15",
                }
            ]
        }

        response = client.get("/api/portfolio", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        assert response.json()["holdings"][0]["ticker"] == "AAPL"
        mock_encryption_service.decrypt_portfolio.assert_called_once_with("encrypted_blob")

    def test_returns_500_on_decryption_failure(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_portfolio.return_value = {"encrypted_data": "bad_data"}
        mock_encryption_service.decrypt_portfolio.side_effect = DecryptionError("Failed")

        response = client.get("/api/portfolio", headers={"X-User-Id": user_id})

        assert response.status_code == 500

    def test_returns_401_without_auth_header(self, client):
        assert client.get("/api/portfolio").status_code == 401

    def test_returns_401_with_invalid_uuid(self, client):
        response = client.get("/api/portfolio", headers={"X-User-Id": "not-a-uuid"})
        assert response.status_code == 401


class TestAddStockToPortfolio:
    def test_adds_stock_to_empty_portfolio(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_stock.return_value = {"ticker": "AAPL"}
        mock_store.get_portfolio.return_value = None
        mock_store.put_portfolio.return_value = {"updated_at": "2025-01-15T10:00:00"}
        mock_encryption_service.encrypt_portfolio.return_value = "new_encrypted"

        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": 50, "buying_price": 175.20},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["holdings"][0]["ticker"] == "AAPL"
        assert data["holdings"][0]["quantity"] == 50

    def test_adds_stock_to_existing_portfolio(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_stock.return_value = {"ticker": "MSFT"}
        mock_store.get_portfolio.return_value = {"encrypted_data": "existing_encrypted"}
        mock_store.put_portfolio.return_value = {"updated_at": "2025-01-15T10:00:00"}
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [
                {
                    "ticker": "AAPL",
                    "quantity": 50,
                    "buying_price": 175.20,
                    "added_date": "2025-03-15",
                }
            ]
        }
        mock_encryption_service.encrypt_portfolio.return_value = "updated_encrypted"

        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "MSFT", "quantity": 30, "buying_price": 420.00},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 200
        assert len(response.json()["holdings"]) == 2

    def test_rejects_ticker_not_in_watchlist(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_stock.return_value = None

        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "FAKE", "quantity": 10, "buying_price": 50.00},
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "payload",
        [
            {"ticker": "AAPL", "quantity": 0, "buying_price": 175.20},
            {"ticker": "AAPL", "quantity": -5, "buying_price": 175.20},
            {"ticker": "AAPL", "quantity": 10, "buying_price": 0},
            {"ticker": "AAPL", "quantity": 10, "buying_price": -50.0},
        ],
    )
    def test_rejects_invalid_holding_values(self, client, user_id, payload):
        response = client.put(
            "/api/portfolio/stocks", json=payload, headers={"X-User-Id": user_id}
        )

        assert response.status_code == 422

    def test_returns_401_without_auth(self, client):
        response = client.put(
            "/api/portfolio/stocks",
            json={"ticker": "AAPL", "quantity": 10, "buying_price": 175.20},
        )

        assert response.status_code == 401


class TestRemoveStockFromPortfolio:
    def test_removes_stock_from_portfolio(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_portfolio.return_value = {"encrypted_data": "encrypted_blob"}
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [
                {"ticker": "AAPL", "quantity": 50, "buying_price": 175.20},
                {"ticker": "MSFT", "quantity": 30, "buying_price": 420.00},
            ]
        }
        mock_encryption_service.encrypt_portfolio.return_value = "updated_encrypted"

        response = client.delete(
            "/api/portfolio/stocks/AAPL", headers={"X-User-Id": user_id}
        )

        assert response.status_code == 200
        assert "removed" in response.json()["message"]
        mock_store.put_portfolio.assert_called_once()

    def test_returns_404_when_portfolio_not_found(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_portfolio.return_value = None

        response = client.delete(
            "/api/portfolio/stocks/AAPL", headers={"X-User-Id": user_id}
        )

        assert response.status_code == 404

    def test_returns_404_when_stock_not_in_portfolio(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_portfolio.return_value = {"encrypted_data": "encrypted_blob"}
        mock_encryption_service.decrypt_portfolio.return_value = {
            "holdings": [{"ticker": "MSFT", "quantity": 30, "buying_price": 420.00}]
        }

        response = client.delete(
            "/api/portfolio/stocks/AAPL", headers={"X-User-Id": user_id}
        )

        assert response.status_code == 404

    def test_returns_500_on_decryption_failure(
        self, client, user_id, mock_store, mock_encryption_service
    ):
        mock_store.get_portfolio.return_value = {"encrypted_data": "bad_data"}
        mock_encryption_service.decrypt_portfolio.side_effect = DecryptionError("Failed")

        response = client.delete(
            "/api/portfolio/stocks/AAPL", headers={"X-User-Id": user_id}
        )

        assert response.status_code == 500

    def test_returns_401_without_auth(self, client):
        assert client.delete("/api/portfolio/stocks/AAPL").status_code == 401
