"""Unit tests for the health check endpoint."""

from datetime import datetime, timedelta, timezone
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
        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        stock_time = recent_time
        news_time = recent_time
        earnings_time = recent_time
        dividend_time = recent_time
        analysis_time = recent_time
        publication_time = recent_time
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
        mock_store.last_earnings_collection.return_value = earnings_time
        mock_store.last_dividend_collection.return_value = dividend_time
        mock_store.last_stock_collection_summary.return_value = stock_summary
        mock_store.last_news_collection_summary.return_value = news_summary
        mock_store.last_analysis.return_value = analysis_time
        mock_store.last_publication.return_value = publication_time

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["components"]["database"] == "ok"
        assert data["components"]["stock_collection"] == "ok"
        assert data["components"]["news_collection"] == "ok"
        assert data["components"]["earnings_collection"] == "ok"
        assert data["components"]["dividend_collection"] == "ok"
        assert data["components"]["analysis"] == "ok"
        assert data["components"]["publication"] == "ok"
        assert data["last_stock_collection"] == stock_time
        assert data["last_news_collection"] == news_time
        assert data["last_earnings_collection"] == earnings_time
        assert data["last_dividend_collection"] == dividend_time
        assert data["last_stock_collection_summary"] == stock_summary
        assert data["last_news_collection_summary"] == news_summary
        assert data["last_analysis"] == analysis_time
        assert data["last_publication"] == publication_time
        assert data["freshness"]["stock_collection"]["status"] == "ok"
        assert data["freshness"]["stock_collection"]["max_age_hours"] == 36
        assert data["freshness"]["news_collection"]["max_age_hours"] == 2

    def test_health_check_returns_degraded_when_db_fails(self, client, mock_store):
        mock_store.ping.side_effect = Exception("Connection refused")

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["components"]["database"] == "error"
        assert data["last_stock_collection"] is None
        assert data["last_news_collection"] is None
        assert data["last_earnings_collection"] is None
        assert data["last_dividend_collection"] is None
        assert data["last_stock_collection_summary"] is None
        assert data["last_news_collection_summary"] is None
        assert data["last_analysis"] is None
        assert data["last_publication"] is None

    def test_health_check_handles_empty_tables(self, client, mock_store):
        mock_store.last_stock_collection.return_value = None
        mock_store.last_news_collection.return_value = None
        mock_store.last_earnings_collection.return_value = None
        mock_store.last_dividend_collection.return_value = None
        mock_store.last_stock_collection_summary.return_value = None
        mock_store.last_news_collection_summary.return_value = None
        mock_store.last_analysis.return_value = None
        mock_store.last_publication.return_value = None

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["components"]["database"] == "ok"
        assert data["components"]["stock_collection"] == "missing"
        assert data["components"]["news_collection"] == "missing"
        assert data["components"]["earnings_collection"] == "missing"
        assert data["components"]["dividend_collection"] == "missing"
        assert data["components"]["analysis"] == "missing"
        assert data["components"]["publication"] == "missing"
        assert data["last_stock_collection"] is None
        assert data["last_news_collection"] is None
        assert data["last_earnings_collection"] is None
        assert data["last_dividend_collection"] is None
        assert data["last_stock_collection_summary"] is None
        assert data["last_news_collection_summary"] is None
        assert data["last_analysis"] is None
        assert data["last_publication"] is None
        assert data["freshness"]["stock_collection"]["reason"] == (
            "No successful run has been recorded."
        )

    def test_health_check_degrades_when_component_is_stale(self, client, mock_store):
        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        stale_news_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        mock_store.last_stock_collection.return_value = recent_time
        mock_store.last_news_collection.return_value = stale_news_time
        mock_store.last_earnings_collection.return_value = recent_time
        mock_store.last_dividend_collection.return_value = recent_time
        mock_store.last_stock_collection_summary.return_value = {"status": "success"}
        mock_store.last_news_collection_summary.return_value = {"status": "success"}
        mock_store.last_analysis.return_value = recent_time
        mock_store.last_publication.return_value = recent_time

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["components"]["news_collection"] == "stale"
        assert data["freshness"]["news_collection"]["max_age_hours"] == 2
        assert "older than 2 hours" in data["freshness"]["news_collection"]["reason"]
