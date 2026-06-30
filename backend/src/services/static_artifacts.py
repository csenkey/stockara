"""Static dashboard artifact publishing helpers."""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import boto3
import structlog

logger = structlog.get_logger(__name__)


def publish_json_artifact(bucket: str, key: str, payload: dict[str, Any]) -> None:
    """Publish a JSON artifact for CloudFront/S3-backed dashboard views."""
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, default=_json_default, indent=2).encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=300",
    )


def safe_publish_json_artifact(bucket: str, key: str, payload: dict[str, Any]) -> None:
    try:
        publish_json_artifact(bucket, key, payload)
    except Exception as exc:
        logger.warning("static_artifact_publish_failed", key=key, error=str(exc))


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
