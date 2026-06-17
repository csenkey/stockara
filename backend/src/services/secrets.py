"""Secret resolution helpers for external provider credentials."""

import json
import os
from functools import lru_cache
from typing import Any

import boto3
import structlog

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_openai_api_key() -> str | None:
    """Resolve the OpenAI API key from local env or AWS Secrets Manager.

    Local development may set OPENAI_API_KEY directly. Deployed Lambdas should
    set OPENAI_API_KEY_SECRET_NAME and read the value from Secrets Manager.
    """
    direct_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if direct_key:
        return direct_key

    secret_name = os.environ.get("OPENAI_API_KEY_SECRET_NAME", "").strip()
    if not secret_name:
        logger.warning("openai_api_key_not_configured")
        return None

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_name)
        return _extract_secret_string(response)
    except Exception as exc:
        logger.warning(
            "openai_api_key_secret_unavailable",
            secret_name=secret_name,
            error=str(exc),
        )
        return None


def _extract_secret_string(response: dict[str, Any]) -> str | None:
    secret_string = str(response.get("SecretString") or "").strip()
    if not secret_string:
        return None

    if not secret_string.startswith("{"):
        return secret_string

    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError:
        return secret_string

    for key in ("OPENAI_API_KEY", "openai_api_key", "api_key"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None
