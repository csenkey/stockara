"""Unit tests for the user preferences API."""

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.src.api.preferences import router


@pytest.fixture
def app():
    """Create a FastAPI app with the preferences router."""
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
        "backend.src.api.preferences.get_db_connection",
        return_value=MockAsyncContextManager(),
    ):
        yield mock_conn, mock_cursor


class TestGetPreferences:
    """Tests for GET /api/preferences."""

    def test_returns_defaults_when_no_preferences_exist(
        self, client, user_id, mock_db_connection
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None

        response = client.get("/api/preferences", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        data = response.json()
        assert data["preferred_sectors"] == []
        assert data["preferred_sizes"] == []
        assert data["max_risk_level"] == "HIGH"

    def test_returns_stored_preferences(self, client, user_id, mock_db_connection):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {
            "preferred_sectors": ["Technology", "Healthcare"],
            "preferred_sizes": ["blue_chip", "mid_cap"],
            "max_risk_level": "MEDIUM",
        }

        response = client.get("/api/preferences", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        data = response.json()
        assert data["preferred_sectors"] == ["Technology", "Healthcare"]
        assert data["preferred_sizes"] == ["blue_chip", "mid_cap"]
        assert data["max_risk_level"] == "MEDIUM"

    def test_returns_401_without_auth_header(self, client):
        response = client.get("/api/preferences")
        assert response.status_code == 401

    def test_returns_401_with_invalid_uuid(self, client):
        response = client.get("/api/preferences", headers={"X-User-Id": "not-a-uuid"})
        assert response.status_code == 401


class TestUpdatePreferences:
    """Tests for PUT /api/preferences."""

    def test_updates_preferences_successfully(self, client, user_id, mock_db_connection):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {
            "preferred_sectors": ["Technology"],
            "preferred_sizes": ["blue_chip"],
            "max_risk_level": "LOW",
        }

        response = client.put(
            "/api/preferences",
            json={
                "preferred_sectors": ["Technology"],
                "preferred_sizes": ["blue_chip"],
                "max_risk_level": "LOW",
            },
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["preferred_sectors"] == ["Technology"]
        assert data["preferred_sizes"] == ["blue_chip"]
        assert data["max_risk_level"] == "LOW"

    def test_rejects_invalid_sector(self, client, user_id):
        response = client.put(
            "/api/preferences",
            json={
                "preferred_sectors": ["InvalidSector"],
                "preferred_sizes": [],
                "max_risk_level": "HIGH",
            },
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 422

    def test_rejects_invalid_size(self, client, user_id):
        response = client.put(
            "/api/preferences",
            json={
                "preferred_sectors": [],
                "preferred_sizes": ["giant"],
                "max_risk_level": "HIGH",
            },
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 422

    def test_rejects_invalid_risk_level(self, client, user_id):
        response = client.put(
            "/api/preferences",
            json={
                "preferred_sectors": [],
                "preferred_sizes": [],
                "max_risk_level": "EXTREME",
            },
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 422

    def test_accepts_empty_preferences(self, client, user_id, mock_db_connection):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {
            "preferred_sectors": [],
            "preferred_sizes": [],
            "max_risk_level": "HIGH",
        }

        response = client.put(
            "/api/preferences",
            json={
                "preferred_sectors": [],
                "preferred_sizes": [],
                "max_risk_level": "HIGH",
            },
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["preferred_sectors"] == []
        assert data["preferred_sizes"] == []

    def test_returns_401_without_auth(self, client):
        response = client.put(
            "/api/preferences",
            json={
                "preferred_sectors": [],
                "preferred_sizes": [],
                "max_risk_level": "HIGH",
            },
        )
        assert response.status_code == 401
