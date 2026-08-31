"""Tests for the authenticated on-demand holding review API boundary."""

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.analysis.holding_review import HoldingReviewResult, HoldingReviewStatus
from src.api.holding_reviews import current_user_id, router


def _client() -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(router)
    return app, TestClient(app)


def test_holding_review_requires_authenticated_cognito_claims():
    _, client = _client()

    response = client.post(
        "/api/holding-reviews",
        json={"ticker": "AAPL", "quantity": 2, "buying_price": "150"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_authenticated_user_can_request_score_independent_holding_review():
    app, client = _client()
    app.dependency_overrides[current_user_id] = lambda: "cognito-user-123"
    result = HoldingReviewResult(
        status=HoldingReviewStatus.COMPLETED_DEGRADED,
        ticker="AAPL",
        missing_optional_evidence=["replacement_comparison_set"],
    )
    engine = MagicMock()
    engine.review.return_value = result

    with (
        patch("src.api.holding_reviews._build_client", return_value=MagicMock()),
        patch("src.api.holding_reviews.HoldingReviewEngine", return_value=engine),
    ):
        response = client.post(
            "/api/holding-reviews",
            json={
                "ticker": "aapl",
                "quantity": 2,
                "buying_price": "150.00",
                "objective": "income",
            },
        )

    assert response.status_code == 200
    assert response.json()["ticker"] == "AAPL"
    assert response.json()["status"] == "COMPLETED_DEGRADED"
    request = engine.review.call_args.args[0]
    assert request.ticker == "AAPL"
    assert request.objective.value == "income"


def test_invalid_holding_input_is_rejected_before_analysis():
    app, client = _client()
    app.dependency_overrides[current_user_id] = lambda: "cognito-user-123"

    response = client.post(
        "/api/holding-reviews",
        json={"ticker": "AAPL", "quantity": 0, "buying_price": "150"},
    )

    assert response.status_code == 422


def test_application_allows_configured_frontend_origin():
    from src.api.handler import app

    client = TestClient(app)
    response = client.options(
        "/api/holding-reviews",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
