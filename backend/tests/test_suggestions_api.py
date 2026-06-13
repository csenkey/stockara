"""Unit tests for the suggestions API endpoints."""

from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.suggestions import router
from backend.src.models.schemas import Recommendation, RiskLevel, Timeframe
from backend.src.services.encryption_service import DecryptionError
from backend.src.services.suggestion_engine import (
    SuggestionEngineError,
    SuggestionItem,
    SuggestionsResult,
)


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
    with patch("backend.src.api.suggestions.store") as store:
        store.get_portfolio.return_value = None
        store.get_preferences.return_value = None
        yield store


def suggestions_result():
    return SuggestionsResult(
        sell_suggestions=[
            SuggestionItem(
                ticker="AAPL",
                recommendation=Recommendation.SELL,
                risk_level=RiskLevel.MEDIUM,
                timeframe=Timeframe.SHORT_TERM,
                confidence_score=75,
                reasoning="Overvalued",
            )
        ],
        buy_suggestions=[
            SuggestionItem(
                ticker="MSFT",
                recommendation=Recommendation.BUY,
                risk_level=RiskLevel.LOW,
                timeframe=Timeframe.LONG_TERM,
                confidence_score=85,
                reasoning="Strong growth",
            )
        ],
        analysis_date=date(2025, 7, 1),
    )


class TestGetSuggestions:
    def test_returns_suggestions_for_authenticated_user(self, client, user_id, mock_store):
        with patch(
            "backend.src.api.suggestions.generate_suggestions",
            new_callable=AsyncMock,
            return_value=suggestions_result(),
        ):
            response = client.get("/api/suggestions", headers={"X-User-Id": user_id})

        assert response.status_code == 200
        data = response.json()
        assert data["sell_suggestions"][0]["ticker"] == "AAPL"
        assert data["buy_suggestions"][0]["ticker"] == "MSFT"
        assert data["analysis_date"] == "2025-07-01"

    def test_returns_401_without_auth(self, client):
        assert client.get("/api/suggestions").status_code == 401

    def test_returns_401_with_invalid_user_id(self, client):
        response = client.get("/api/suggestions", headers={"X-User-Id": "not-a-uuid"})
        assert response.status_code == 401

    def test_returns_500_on_suggestion_engine_error(self, client, user_id, mock_store):
        with patch(
            "backend.src.api.suggestions.generate_suggestions",
            new_callable=AsyncMock,
            side_effect=SuggestionEngineError("No analysis data available"),
        ):
            response = client.get("/api/suggestions", headers={"X-User-Id": user_id})

        assert response.status_code == 500
        assert "No analysis data available" in response.json()["detail"]

    def test_applies_sector_filter_from_query_param(self, client, user_id, mock_store):
        with patch(
            "backend.src.api.suggestions.generate_suggestions",
            new_callable=AsyncMock,
            return_value=SuggestionsResult([], [], date(2025, 7, 1)),
        ) as mock_gen:
            response = client.get(
                "/api/suggestions?sector=Technology", headers={"X-User-Id": user_id}
            )

        assert response.status_code == 200
        assert mock_gen.call_args.kwargs["preferences"].preferred_sectors == ["Technology"]

    def test_rejects_invalid_company_size_filter(self, client, user_id, mock_store):
        response = client.get(
            "/api/suggestions?company_size=invalid", headers={"X-User-Id": user_id}
        )

        assert response.status_code == 400

    def test_rejects_invalid_max_risk_filter(self, client, user_id, mock_store):
        response = client.get(
            "/api/suggestions?max_risk=EXTREME", headers={"X-User-Id": user_id}
        )

        assert response.status_code == 400

    def test_returns_500_on_portfolio_decryption_failure(
        self, client, user_id, mock_store
    ):
        mock_store.get_portfolio.return_value = {"encrypted_data": "bad_data"}

        with patch("backend.src.api.suggestions.EncryptionService") as mock_enc_cls:
            mock_enc_cls.return_value.decrypt_portfolio.side_effect = DecryptionError(
                "Failed"
            )
            response = client.get("/api/suggestions", headers={"X-User-Id": user_id})

        assert response.status_code == 500


class TestGetStockAnalysis:
    def test_returns_latest_analysis(self, client, mock_store):
        mock_store.latest_analysis_for_ticker.return_value = {
            "ticker": "AAPL",
            "analysis_date": date(2025, 7, 1),
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "LOW",
            "confidence_score": 82,
            "reasoning": "Strong fundamentals",
            "created_at": datetime(2025, 7, 1, 10, 0, 0),
        }

        response = client.get("/api/stocks/AAPL/analysis")

        assert response.status_code == 200
        data = response.json()
        assert data["ticker"] == "AAPL"
        assert data["analysis_date"] == "2025-07-01"

    def test_returns_404_when_no_analysis_exists(self, client, mock_store):
        mock_store.latest_analysis_for_ticker.return_value = None

        response = client.get("/api/stocks/FAKE/analysis")

        assert response.status_code == 404

    def test_normalizes_ticker_to_uppercase(self, client, mock_store):
        mock_store.latest_analysis_for_ticker.return_value = {
            "ticker": "AAPL",
            "analysis_date": date(2025, 7, 1),
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "MEDIUM",
            "confidence_score": 70,
            "reasoning": None,
            "created_at": None,
        }

        response = client.get("/api/stocks/aapl/analysis")

        assert response.status_code == 200
        mock_store.latest_analysis_for_ticker.assert_called_once_with("AAPL")

    def test_no_auth_required(self, client, mock_store):
        mock_store.latest_analysis_for_ticker.return_value = {
            "ticker": "MSFT",
            "analysis_date": date(2025, 7, 1),
            "short_term_recommendation": "SELL",
            "long_term_recommendation": "SELL",
            "risk_level": "HIGH",
            "confidence_score": 60,
            "reasoning": "Declining revenue",
            "created_at": datetime(2025, 7, 1, 12, 0, 0),
        }

        response = client.get("/api/stocks/MSFT/analysis")

        assert response.status_code == 200
