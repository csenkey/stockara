"""Deployment-stage naming helpers for CDK resources."""

import re


MAX_STAGE_LENGTH = 24


def sanitize_stage(value: str | None) -> str:
    """Return a CloudFormation/resource-name safe deployment stage."""
    stage = (value or "prod").strip().lower()
    stage = re.sub(r"[^a-z0-9-]+", "-", stage)
    stage = re.sub(r"-+", "-", stage).strip("-")

    if stage in {"", "main", "master", "production"}:
        return "prod"

    stage = stage[:MAX_STAGE_LENGTH].strip("-")
    return stage or "prod"


def is_prod(stage: str) -> bool:
    return sanitize_stage(stage) == "prod"


def stack_id(base_id: str, stage: str) -> str:
    """Preserve existing prod stack names and suffix non-prod stacks."""
    return base_id if is_prod(stage) else f"{base_id}-{stage}"


def resource_name(stage: str, prod_name: str, suffix: str) -> str:
    """Preserve existing prod physical names and prefix non-prod resources."""
    return prod_name if is_prod(stage) else f"stockara-{sanitize_stage(stage)}-{suffix}"
