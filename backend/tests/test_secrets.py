"""Tests for provider secret resolution."""

from unittest.mock import MagicMock, patch

from backend.src.services.secrets import get_openai_api_key


def setup_function():
    get_openai_api_key.cache_clear()


def teardown_function():
    get_openai_api_key.cache_clear()


def test_get_openai_api_key_prefers_direct_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local")
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_NAME", "stockara/prod/openai-api-key-current")

    assert get_openai_api_key() == "sk-local"


def test_get_openai_api_key_fetches_plain_secret(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_NAME", "stockara/prod/openai-api-key-current")
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {"SecretString": "sk-secret"}

    with patch("backend.src.services.secrets.boto3.client", return_value=secrets_client):
        assert get_openai_api_key() == "sk-secret"

    secrets_client.get_secret_value.assert_called_once_with(
        SecretId="stockara/prod/openai-api-key-current"
    )


def test_get_openai_api_key_fetches_json_secret(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_NAME", "stockara/prod/openai-api-key-current")
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {
        "SecretString": '{"api_key": "sk-json"}'
    }

    with patch("backend.src.services.secrets.boto3.client", return_value=secrets_client):
        assert get_openai_api_key() == "sk-json"


def test_get_openai_api_key_accepts_single_custom_json_field(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_NAME", "stockara/prod/openai-api-key-current")
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {
        "SecretString": '{"openai-api-key-current": "sk-custom"}'
    }

    with patch("backend.src.services.secrets.boto3.client", return_value=secrets_client):
        assert get_openai_api_key() == "sk-custom"


def test_get_openai_api_key_rejects_ambiguous_json_secret(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_NAME", "stockara/prod/openai-api-key-current")
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {
        "SecretString": '{"username": "stockara", "password": "sk-secret"}'
    }

    with patch("backend.src.services.secrets.boto3.client", return_value=secrets_client):
        assert get_openai_api_key() is None


def test_get_openai_api_key_returns_none_without_configuration(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_SECRET_NAME", raising=False)

    assert get_openai_api_key() is None


def test_get_openai_api_key_returns_none_when_secret_unavailable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_NAME", "stockara/prod/openai-api-key-current")
    secrets_client = MagicMock()
    secrets_client.get_secret_value.side_effect = RuntimeError("access denied")

    with patch("backend.src.services.secrets.boto3.client", return_value=secrets_client):
        assert get_openai_api_key() is None
