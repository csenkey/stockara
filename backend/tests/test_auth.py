"""Unit tests for the Cognito authentication API."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.src.api.auth import router, validate_password_policy, get_current_user


@pytest.fixture
def app():
    """Create a FastAPI app with the auth router."""
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

    with patch("backend.src.api.auth.get_db_connection", return_value=MockAsyncContextManager()):
        yield mock_conn, mock_cursor


@pytest.fixture
def mock_cognito_client():
    """Mock the Cognito client."""
    mock_client = MagicMock()
    with patch("backend.src.api.auth.get_cognito_client", return_value=mock_client):
        yield mock_client


# --- Password Validation Tests ---


class TestPasswordValidation:
    """Tests for password policy validation."""

    def test_valid_password(self):
        """Password meeting all criteria passes validation."""
        errors = validate_password_policy("ValidPass1")
        assert errors == []

    def test_password_too_short(self):
        """Password shorter than 8 chars fails."""
        errors = validate_password_policy("Short1A")
        assert any("at least 8" in e for e in errors)

    def test_password_too_long(self):
        """Password longer than 128 chars fails."""
        errors = validate_password_policy("A" * 127 + "a1" + "x" * 1)
        # 130 chars total
        long_pass = "Aa1" + "x" * 126
        errors = validate_password_policy(long_pass)
        # This is 129 chars, should fail
        assert any("at most 128" in e for e in errors)

    def test_password_no_uppercase(self):
        """Password without uppercase fails."""
        errors = validate_password_policy("lowercase1")
        assert any("uppercase" in e for e in errors)

    def test_password_no_lowercase(self):
        """Password without lowercase fails."""
        errors = validate_password_policy("UPPERCASE1")
        assert any("lowercase" in e for e in errors)

    def test_password_no_digit(self):
        """Password without digit fails."""
        errors = validate_password_policy("NoDigitHere")
        assert any("digit" in e for e in errors)

    def test_password_multiple_errors(self):
        """Password with multiple issues returns multiple errors."""
        errors = validate_password_policy("short")
        assert len(errors) >= 2  # too short + no uppercase + no digit


# --- Registration Tests ---


class TestRegister:
    """Tests for POST /api/auth/register."""

    def test_register_success(self, client, mock_cognito_client, mock_db_connection):
        """Successful registration returns 201 with user_id."""
        mock_cognito_client.sign_up.return_value = {"UserSub": "test-uuid-123"}
        mock_cognito_client.admin_confirm_sign_up.return_value = {}

        response = client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "ValidPass1"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "Registration successful"
        assert data["user_id"] == "test-uuid-123"

    def test_register_invalid_password_policy(self, client):
        """Registration with invalid password (no uppercase/digit) returns 400."""
        response = client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "alllowercase"},
        )

        assert response.status_code == 400

    def test_register_duplicate_email(self, client, mock_cognito_client):
        """Registration with existing email returns 409."""
        mock_cognito_client.sign_up.side_effect = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "User exists"}},
            "SignUp",
        )

        response = client.post(
            "/api/auth/register",
            json={"email": "existing@example.com", "password": "ValidPass1"},
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    def test_register_creates_local_user(self, client, mock_cognito_client, mock_db_connection):
        """Registration creates a record in the local users table."""
        mock_cognito_client.sign_up.return_value = {"UserSub": "user-uuid-456"}
        mock_cognito_client.admin_confirm_sign_up.return_value = {}
        _, mock_cursor = mock_db_connection

        response = client.post(
            "/api/auth/register",
            json={"email": "new@example.com", "password": "ValidPass1"},
        )

        assert response.status_code == 201
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "INSERT INTO users" in call_args[0][0]
        assert call_args[0][1] == ("user-uuid-456", "new@example.com")


# --- Login Tests ---


class TestLogin:
    """Tests for POST /api/auth/login."""

    def test_login_success(self, client, mock_cognito_client):
        """Successful login returns access token."""
        mock_cognito_client.initiate_auth.return_value = {
            "AuthenticationResult": {
                "AccessToken": "test-access-token",
                "ExpiresIn": 1800,
            }
        }

        response = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "password": "ValidPass1"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test-access-token"
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 1800

    def test_login_invalid_credentials_wrong_password(self, client, mock_cognito_client):
        """Failed login with wrong password returns generic 401."""
        mock_cognito_client.initiate_auth.side_effect = ClientError(
            {"Error": {"Code": "NotAuthorizedException", "Message": "Incorrect"}},
            "InitiateAuth",
        )

        response = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "password": "WrongPass1"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid credentials"

    def test_login_user_not_found_generic_error(self, client, mock_cognito_client):
        """Failed login with non-existent user returns same generic 401."""
        mock_cognito_client.initiate_auth.side_effect = ClientError(
            {"Error": {"Code": "UserNotFoundException", "Message": "User not found"}},
            "InitiateAuth",
        )

        response = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "SomePass1"},
        )

        assert response.status_code == 401
        # Same message as wrong password — doesn't reveal email existence
        assert response.json()["detail"] == "Invalid credentials"

    def test_login_account_locked(self, client, mock_cognito_client):
        """Account locked after too many attempts returns 429."""
        mock_cognito_client.initiate_auth.side_effect = ClientError(
            {"Error": {"Code": "TooManyFailedAttemptsException", "Message": "Locked"}},
            "InitiateAuth",
        )

        response = client.post(
            "/api/auth/login",
            json={"email": "locked@example.com", "password": "Attempt1"},
        )

        assert response.status_code == 429
        assert "temporarily locked" in response.json()["detail"]


# --- JWT Validation Tests ---


class TestJWTValidation:
    """Tests for JWT token verification dependency."""

    def test_verify_token_no_auth_header(self, client, app):
        """Request without auth header returns 401 or 403."""
        @app.get("/protected")
        async def protected(user_id: str = pytest.importorskip("fastapi").Depends(get_current_user)):
            return {"user_id": user_id}

        response = client.get("/protected")
        assert response.status_code in (401, 403)

    @patch("backend.src.api.auth.verify_jwt_token")
    def test_verify_token_valid(self, mock_verify, client, app):
        """Valid token returns user_id from sub claim."""
        mock_verify.return_value = {"sub": "user-123", "exp": 9999999999}

        @app.get("/protected2")
        async def protected(user_id: str = pytest.importorskip("fastapi").Depends(get_current_user)):
            return {"user_id": user_id}

        response = client.get("/protected2", headers={"Authorization": "Bearer valid-token"})
        assert response.status_code == 200
        assert response.json()["user_id"] == "user-123"

    @patch("backend.src.api.auth.verify_jwt_token")
    def test_verify_token_invalid(self, mock_verify, client, app):
        """Invalid token returns 401."""
        from fastapi import HTTPException
        mock_verify.side_effect = HTTPException(status_code=401, detail="Invalid token")

        @app.get("/protected3")
        async def protected(user_id: str = pytest.importorskip("fastapi").Depends(get_current_user)):
            return {"user_id": user_id}

        response = client.get("/protected3", headers={"Authorization": "Bearer bad-token"})
        assert response.status_code == 401

    @patch("backend.src.api.auth.verify_jwt_token")
    def test_verify_token_expired(self, mock_verify, client, app):
        """Expired token returns 401 with 'Token expired' detail."""
        from fastapi import HTTPException
        mock_verify.side_effect = HTTPException(status_code=401, detail="Token expired")

        @app.get("/protected4")
        async def protected(user_id: str = pytest.importorskip("fastapi").Depends(get_current_user)):
            return {"user_id": user_id}

        response = client.get("/protected4", headers={"Authorization": "Bearer expired-token"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Token expired"
