"""Unit tests for the stock watchlist CRUD API."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.stocks import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def mock_store():
    with patch("backend.src.api.stocks.store") as store:
        yield store


def stock_row(**overrides):
    row = {
        "ticker": "AAPL",
        "company_name": "Apple Inc",
        "sector": "Technology",
        "company_size": "blue_chip",
        "added_at": "2025-01-01T00:00:00",
        "is_active": True,
    }
    row.update(overrides)
    return row


class TestListStocks:
    def test_list_stocks_empty(self, client, mock_store):
        mock_store.list_stocks.return_value = []

        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert response.json() == {"stocks": [], "total": 0}
        mock_store.list_stocks.assert_called_once_with(
            sector=None, company_size=None, is_active=None
        )

    def test_list_stocks_with_results(self, client, mock_store):
        mock_store.list_stocks.return_value = [stock_row()]

        response = client.get("/api/stocks")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["stocks"][0]["ticker"] == "AAPL"

    def test_list_stocks_filter_by_sector(self, client, mock_store):
        mock_store.list_stocks.return_value = []

        response = client.get("/api/stocks?sector=Technology")

        assert response.status_code == 200
        mock_store.list_stocks.assert_called_once_with(
            sector="Technology", company_size=None, is_active=None
        )

    def test_list_stocks_filter_by_company_size(self, client, mock_store):
        mock_store.list_stocks.return_value = []

        response = client.get("/api/stocks?company_size=blue_chip")

        assert response.status_code == 200
        mock_store.list_stocks.assert_called_once_with(
            sector=None, company_size="blue_chip", is_active=None
        )

    def test_list_stocks_invalid_sector(self, client, mock_store):
        response = client.get("/api/stocks?sector=InvalidSector")

        assert response.status_code == 400
        mock_store.list_stocks.assert_not_called()


class TestAddStock:
    def test_add_stock_success(self, client, mock_store):
        mock_store.get_stock.return_value = None
        mock_store.put_stock.return_value = stock_row()

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
        assert response.json()["ticker"] == "AAPL"
        mock_store.put_stock.assert_called_once()

    def test_add_stock_duplicate(self, client, mock_store):
        mock_store.get_stock.return_value = stock_row()

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
        mock_store.put_stock.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [
            {"ticker": "AAPL", "company_name": "Apple Inc", "company_size": "blue_chip"},
            {"ticker": "AAPL", "company_name": "Apple Inc", "sector": "Technology"},
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "InvalidSector",
                "company_size": "blue_chip",
            },
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "sector": "Technology",
                "company_size": "mega_corp",
            },
        ],
    )
    def test_add_stock_validation_errors(self, client, mock_store, payload):
        response = client.post("/api/stocks", json=payload)

        assert response.status_code == 422


class TestRemoveStock:
    def test_remove_stock_success(self, client, mock_store):
        mock_store.get_stock.return_value = stock_row()

        response = client.delete("/api/stocks/AAPL")

        assert response.status_code == 200
        assert "removed" in response.json()["message"]
        mock_store.delete_stock.assert_called_once_with("AAPL")

    def test_remove_stock_not_found(self, client, mock_store):
        mock_store.get_stock.return_value = None

        response = client.delete("/api/stocks/FAKE")

        assert response.status_code == 404


class TestUpdateStock:
    def test_update_stock_success(self, client, mock_store):
        mock_store.update_stock.return_value = stock_row(
            company_name="Apple Inc Updated"
        )

        response = client.put(
            "/api/stocks/AAPL",
            json={"company_name": "Apple Inc Updated"},
        )

        assert response.status_code == 200
        assert response.json()["company_name"] == "Apple Inc Updated"

    def test_update_stock_not_found(self, client, mock_store):
        mock_store.update_stock.side_effect = KeyError("FAKE")

        response = client.put("/api/stocks/FAKE", json={"company_name": "Updated"})

        assert response.status_code == 404

    def test_update_stock_no_fields(self, client, mock_store):
        response = client.put("/api/stocks/AAPL", json={})

        assert response.status_code == 400
        mock_store.update_stock.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [{"sector": "InvalidSector"}, {"company_size": "mega_corp"}],
    )
    def test_update_stock_validation_errors(self, client, mock_store, payload):
        response = client.put("/api/stocks/AAPL", json=payload)

        assert response.status_code == 422
