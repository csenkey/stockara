"""FastAPI router for health check endpoint."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import structlog

from backend.src.db.connection import store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


class ComponentStatus(BaseModel):
    """Status of individual system components."""

    database: str


class HealthResponse(BaseModel):
    """Response model for the health check endpoint."""

    status: str
    components: ComponentStatus
    last_stock_collection: Optional[str] = None
    last_news_collection: Optional[str] = None
    last_analysis: Optional[str] = None


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Return system health status including component states and last batch timestamps.

    No authentication required. Returns the operational status of all components
    and external dependencies.
    """
    db_status = "ok"
    last_stock_collection: Optional[str] = None
    last_news_collection: Optional[str] = None
    last_analysis: Optional[str] = None

    try:
        store.ping()
        last_stock_collection = store.last_stock_collection()
        last_news_collection = store.last_news_collection()
        last_analysis = store.last_analysis()

    except Exception as e:
        db_status = "error"
        logger.error("Health check database error", error=str(e))

    overall_status = "ok" if db_status == "ok" else "degraded"

    return HealthResponse(
        status=overall_status,
        components=ComponentStatus(database=db_status),
        last_stock_collection=last_stock_collection,
        last_news_collection=last_news_collection,
        last_analysis=last_analysis,
    )
