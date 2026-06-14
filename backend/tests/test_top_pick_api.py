"""Unit tests for the public top-pick API."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.top_pick import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def mock_store():
    with patch("backend.src.api.top_pick.store") as store:
        yield store


def test_returns_latest_top_pick(client, mock_store):
    mock_store.latest_top_pick.return_value = {
        "pick_date": "2025-07-02",
        "ticker": "NVDA",
        "company_name": "NVIDIA Corporation",
        "reasoning": "Strong business momentum and high-confidence BUY signal.",
        "analysis_date": "2025-07-01",
        "generated_at": "2025-07-02T06:00:00",
    }

    response = client.get("/api/top-pick")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "NVDA"
    assert data["pick_date"] == "2025-07-02"


def test_returns_404_when_no_top_pick_exists(client, mock_store):
    mock_store.latest_top_pick.return_value = None

    response = client.get("/api/top-pick")

    assert response.status_code == 404
