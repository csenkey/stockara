"""Public API for the daily Stockara top pick."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.src.db.connection import store

router = APIRouter(prefix="/api/top-pick", tags=["top-pick"])


class TopPickResponse(BaseModel):
    """Public daily top-pick content."""

    pick_date: str
    ticker: str
    company_name: Optional[str] = None
    reasoning: str
    analysis_date: Optional[str] = None
    generated_at: Optional[str] = None


@router.get("", response_model=TopPickResponse)
async def get_top_pick():
    """Return the latest generated public daily top pick."""
    row = store.latest_top_pick()
    if not row:
        raise HTTPException(status_code=404, detail="No top pick has been published")
    return TopPickResponse(**row)
