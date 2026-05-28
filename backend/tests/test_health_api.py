"""Unit tests for the health check endpoint."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.src.api.health import router


@pytest.fixture
def app():
    """Create a FastAPI app with the health router."""
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

    class MockAsyncContextManager:
        def __init__(self):
            self.conn = mock_conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *args):
            pass

    with patch("backend.src.api.health.get_db_connection", return_value=MockAsyncContextManager()):
        yield mock_conn, mock_cursor


class TestHealthCheck:
    """Tests for GET /api/health endpoint."""

    def test_health_check_returns_ok_when_db_healthy(self, client, mock_db_connection):
        """Health check returns ok status when database is accessible."""
        mock_conn, mock_cursor = mock_db_connection
        stock_time = datetime(2025, 1, 15, 10, 0, 0)
        news_time = datetime(2025, 1, 15, 9, 30, 0)
        analysis_time = datetime(2025, 1, 15, 8, 0, 0)

        mock_cursor.fetchone.side_effect = [
            {"latest": stock_time},
            {"latest": news_time},
            {"latest": analysis_time},
        ]

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["components"]["database"] == "ok"
        assert data["last_stock_collection"] == stock_time.isoformat()
        assert data["last_news_collection"] == news_time.isoformat()
        assert data["last_analysis"] == analysis_time.isoformat()

    def test_health_check_returns_degraded_when_db_fails(self, client):
        """Health check returns degraded status when database is unreachable."""

        class MockAsyncContextManager:
            async def __aenter__(self):
                raise Exception("Connection refused")

            async def __aexit__(self, *args):
                pass

        with patch(
            "backend.src.api.health.get_db_connection",
            return_value=MockAsyncContextManager(),
        ):
            response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["components"]["database"] == "error"
        assert data["last_stock_collection"] is None
        assert data["last_news_collection"] is None
        assert data["last_analysis"] is None

    def test_health_check_handles_empty_tables(self, client, mock_db_connection):
        """Health check handles case where no data has been collected yet."""
        mock_conn, mock_cursor = mock_db_connection
        mock_cursor.fetchone.side_effect = [
            {"latest": None},
            {"latest": None},
            {"latest": None},
        ]

        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["components"]["database"] == "ok"
        assert data["last_stock_collection"] is None
        assert data["last_news_collection"] is None
        assert data["last_analysis"] is None
