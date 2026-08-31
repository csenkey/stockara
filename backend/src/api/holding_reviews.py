"""Authenticated API for synchronous on-demand holding reviews."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.analysis.holding_review import (
    HoldingReviewEngine,
    HoldingReviewRequest,
    _build_client,
)
from src.db.connection import store

router = APIRouter(prefix="/api/holding-reviews", tags=["holding-reviews"])


def current_user_id(request: Request) -> str:
    """Read claims already verified by the API Gateway Cognito authorizer."""
    event: dict[str, Any] = request.scope.get("aws.event") or {}
    claims = (
        event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    )
    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


@router.post("")
def create_holding_review(
    payload: HoldingReviewRequest,
    _user_id: str = Depends(current_user_id),
) -> dict[str, Any]:
    """Analyze one user-supplied holding without daily score/shortlist gating."""
    result = HoldingReviewEngine(store, _build_client()).review(payload)
    if result.status.value == "FAILED":
        raise HTTPException(status_code=503, detail=result.model_dump(mode="json"))
    return result.model_dump(mode="json")
