"""Unit tests for the health check endpoint."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.health import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def mock_store():
    with patch("backend.src.api.health.store") as store:
        yield store


class TestHealthCheck:
    def test_health_check_returns_ok_when_db_healthy(self, client, mock_store):
        stock_time = "2025-01-15T10:00:00"
        news_time = "2025-01-15T09:30:00"
        analysis_time = "2025-01-15T08:00:00"
        stock_summary = {
            "status": "partial",
            "selected_ticker_count": 25,
            "failed_ticker_count": 2,
        }
        news_summary = {
            "status": "success",
            "sources_available": 2,
            "sources_failed": 0,
        }
        mock_store.last_stock_collection.return_value = stock_time
        mock_store.last_news_collection.return_value = news_time
        mock_store.last_stock_collection_summary.return_value = stock_summary
        mock_store.last_news_collection_summary.return_value = news_summary
        mock_store.last_analysis.return_value = analysis_time

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["components"]["database"] == "ok"
        assert data["last_stock_collection"] == stock_time
        assert data["last_news_collection"] == news_time
        assert data["last_stock_collection_summary"] == stock_summary
        assert data["last_news_collection_summary"] == news_summary
        assert data["last_analysis"] == analysis_time

    def test_health_check_returns_degraded_when_db_fails(self, client, mock_store):
        mock_store.ping.side_effect = Exception("Connection refused")

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["components"]["database"] == "error"
        assert data["last_stock_collection"] is None
        assert data["last_news_collection"] is None
        assert data["last_stock_collection_summary"] is None
        assert data["last_news_collection_summary"] is None
        assert data["last_analysis"] is None

    def test_health_check_handles_empty_tables(self, client, mock_store):
        mock_store.last_stock_collection.return_value = None
        mock_store.last_news_collection.return_value = None
        mock_store.last_stock_collection_summary.return_value = None
        mock_store.last_news_collection_summary.return_value = None
        mock_store.last_analysis.return_value = None

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["components"]["database"] == "ok"
        assert data["last_stock_collection"] is None
        assert data["last_news_collection"] is None
        assert data["last_stock_collection_summary"] is None
        assert data["last_news_collection_summary"] is None
        assert data["last_analysis"] is None
