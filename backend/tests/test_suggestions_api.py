"""Unit tests for the suggestions API endpoints."""

import pytest
from datetime import date, datetime
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.src.api.suggestions import router
from backend.src.models.schemas import (
    Portfolio,
    PortfolioHolding,
    Recommendation,
    RiskLevel,
    Timeframe,
    UserPreferences,
)
from backend.src.services.encryption_service import DecryptionError
from backend.src.services.suggestion_engine import (
    SuggestionEngineError,
    SuggestionItem,
    SuggestionsResult,
)


@pytest.fixture
def app():
    """Create a FastAPI app with the suggestions router."""
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
        "backend.src.api.suggestions.get_db_connection",
        return_value=MockAsyncContextManager(),
    ):
        yield mock_conn, mock_cursor


class TestGetSuggestions:
    """Tests for GET /api/suggestions."""

    def test_returns_suggestions_for_authenticated_user(
        self, client, user_id, mock_db_connection
    ):
        _, mock_cursor = mock_db_connection

        # First call: portfolio query (no portfolio)
        # Second call: preferences query (no prefs)
        mock_cursor.fetchone.side_effect = [None, None]

        mock_result = SuggestionsResult(
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

        with patch(
            "backend.src.api.suggestions.generate_suggestions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client.get(
                "/api/suggestions", headers={"X-User-Id": user_id}
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["sell_suggestions"]) == 1
        assert data["sell_suggestions"][0]["ticker"] == "AAPL"
        assert data["sell_suggestions"][0]["recommendation"] == "SELL"
        assert len(data["buy_suggestions"]) == 1
        assert data["buy_suggestions"][0]["ticker"] == "MSFT"
        assert data["buy_suggestions"][0]["recommendation"] == "BUY"
        assert data["analysis_date"] == "2025-07-01"

    def test_returns_401_without_auth(self, client):
        response = client.get("/api/suggestions")
        assert response.status_code == 401

    def test_returns_401_with_invalid_user_id(self, client):
        response = client.get(
            "/api/suggestions", headers={"X-User-Id": "not-a-uuid"}
        )
        assert response.status_code == 401

    def test_returns_500_on_suggestion_engine_error(
        self, client, user_id, mock_db_connection
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.side_effect = [None, None]

        with patch(
            "backend.src.api.suggestions.generate_suggestions",
            new_callable=AsyncMock,
            side_effect=SuggestionEngineError("No analysis data available"),
        ):
            response = client.get(
                "/api/suggestions", headers={"X-User-Id": user_id}
            )

        assert response.status_code == 500
        assert "No analysis data available" in response.json()["detail"]

    def test_applies_sector_filter_from_query_param(
        self, client, user_id, mock_db_connection
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.side_effect = [None, None]

        mock_result = SuggestionsResult(
            sell_suggestions=[],
            buy_suggestions=[],
            analysis_date=date(2025, 7, 1),
        )

        with patch(
            "backend.src.api.suggestions.generate_suggestions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_gen:
            response = client.get(
                "/api/suggestions?sector=Technology",
                headers={"X-User-Id": user_id},
            )

        assert response.status_code == 200
        # Verify preferences passed to generate_suggestions have sector override
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs["preferences"].preferred_sectors == ["Technology"]

    def test_rejects_invalid_company_size_filter(
        self, client, user_id, mock_db_connection
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.side_effect = [None, None]

        response = client.get(
            "/api/suggestions?company_size=invalid",
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 400
        assert "Invalid company_size" in response.json()["detail"]

    def test_rejects_invalid_max_risk_filter(
        self, client, user_id, mock_db_connection
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.side_effect = [None, None]

        response = client.get(
            "/api/suggestions?max_risk=EXTREME",
            headers={"X-User-Id": user_id},
        )

        assert response.status_code == 400
        assert "Invalid max_risk" in response.json()["detail"]

    def test_returns_500_on_portfolio_decryption_failure(
        self, client, user_id, mock_db_connection
    ):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {"encrypted_data": "bad_data"}

        with patch(
            "backend.src.api.suggestions.EncryptionService"
        ) as mock_enc_cls:
            mock_enc_cls.return_value.decrypt_portfolio.side_effect = DecryptionError(
                "Failed"
            )
            response = client.get(
                "/api/suggestions", headers={"X-User-Id": user_id}
            )

        assert response.status_code == 500


class TestGetStockAnalysis:
    """Tests for GET /api/stocks/{ticker}/analysis."""

    def test_returns_latest_analysis(self, client, mock_db_connection):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {
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
        assert data["short_term_recommendation"] == "BUY"
        assert data["long_term_recommendation"] == "HOLD"
        assert data["risk_level"] == "LOW"
        assert data["confidence_score"] == 82
        assert data["reasoning"] == "Strong fundamentals"

    def test_returns_404_when_no_analysis_exists(self, client, mock_db_connection):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None

        response = client.get("/api/stocks/FAKE/analysis")

        assert response.status_code == 404
        assert "No analysis found" in response.json()["detail"]

    def test_normalizes_ticker_to_uppercase(self, client, mock_db_connection):
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {
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
        # Verify the query used uppercase ticker
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert call_args[0][1] == ("AAPL",)

    def test_no_auth_required(self, client, mock_db_connection):
        """The analysis endpoint is public — no auth header needed."""
        _, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = {
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
