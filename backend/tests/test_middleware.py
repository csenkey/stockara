"""Unit tests for request duration logging middleware."""

import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.src.api.middleware import RequestDurationLoggingMiddleware


@pytest.fixture
def app():
    """Create a FastAPI app with the middleware."""
    app = FastAPI()
    app.add_middleware(RequestDurationLoggingMiddleware)

    @app.get("/fast")
    async def fast_endpoint():
        return {"result": "ok"}

    @app.get("/slow")
    async def slow_endpoint():
        time.sleep(0.01)
        return {"result": "ok"}

    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


class TestRequestDurationLoggingMiddleware:
    """Tests for the request duration logging middleware."""

    def test_logs_request_duration_for_normal_request(self, client):
        """Middleware logs request duration for requests completing within threshold."""
        with patch("backend.src.api.middleware.logger") as mock_logger:
            response = client.get("/fast")

        assert response.status_code == 200
        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert "Request completed" in call_kwargs[0]
        assert "duration_ms" in call_kwargs[1]
        assert call_kwargs[1]["method"] == "GET"
        assert call_kwargs[1]["path"] == "/fast"
        assert call_kwargs[1]["status_code"] == 200

    def test_logs_warning_for_slow_request(self, client):
        """Middleware logs warning when request exceeds 5s threshold."""
        with patch("backend.src.api.middleware.logger") as mock_logger:
            with patch("backend.src.api.middleware.time") as mock_time_module:
                # Simulate a request that takes 6 seconds
                mock_time_module.perf_counter.side_effect = [0.0, 6.0]
                response = client.get("/fast")

        assert response.status_code == 200
        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        assert "Slow request detected" in call_kwargs[0]
        assert call_kwargs[1]["duration_ms"] == 6000.0

    def test_middleware_does_not_break_response(self, client):
        """Middleware passes through the response unchanged."""
        response = client.get("/fast")
        assert response.status_code == 200
        assert response.json() == {"result": "ok"}
