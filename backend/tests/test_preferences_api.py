"""Unit tests for the user preferences API."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.preferences import router


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
    with patch("backend.src.api.preferences.store") as store:
        yield store


class TestGetPreferences:
    def test_returns_defaults_when_no_preferences_exist(self, client, user_id, mock_store):
        mock_store.get_preferences.return_value = None

        response = client.get("/api/preferences", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        assert response.json() == {
            "preferred_sectors": [],
            "preferred_sizes": [],
            "max_risk_level": "HIGH",
        }

    def test_returns_stored_preferences(self, client, user_id, mock_store):
        mock_store.get_preferences.return_value = {
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
        assert client.get("/api/preferences").status_code == 401

    def test_returns_401_with_invalid_uuid(self, client):
        response = client.get("/api/preferences", headers={"X-User-Id": "not-a-uuid"})
        assert response.status_code == 401


class TestUpdatePreferences:
    def test_updates_preferences_successfully(self, client, user_id, mock_store):
        mock_store.put_preferences.return_value = {
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
        assert response.json()["max_risk_level"] == "LOW"

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "preferred_sectors": ["InvalidSector"],
                "preferred_sizes": [],
                "max_risk_level": "HIGH",
            },
            {
                "preferred_sectors": [],
                "preferred_sizes": ["giant"],
                "max_risk_level": "HIGH",
            },
            {
                "preferred_sectors": [],
                "preferred_sizes": [],
                "max_risk_level": "EXTREME",
            },
        ],
    )
    def test_rejects_invalid_preferences(self, client, user_id, payload):
        response = client.put(
            "/api/preferences", json=payload, headers={"X-User-Id": user_id}
        )

        assert response.status_code == 422

    def test_accepts_empty_preferences(self, client, user_id, mock_store):
        mock_store.put_preferences.return_value = {
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
        assert response.json()["preferred_sectors"] == []

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
