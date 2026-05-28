"""FastAPI router for health check endpoint."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

import structlog

from backend.src.db.connection import get_db_connection

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
        async with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check DB connectivity
                cur.execute("SELECT 1")

                # Get latest stock data collection timestamp
                cur.execute(
                    "SELECT MAX(collected_at) as latest FROM stock_data"
                )
                row = cur.fetchone()
                if row and row["latest"]:
                    last_stock_collection = row["latest"].isoformat()

                # Get latest news collection timestamp
                cur.execute(
                    "SELECT MAX(collected_at) as latest FROM news_summaries"
                )
                row = cur.fetchone()
                if row and row["latest"]:
                    last_news_collection = row["latest"].isoformat()

                # Get latest analysis timestamp
                cur.execute(
                    "SELECT MAX(created_at) as latest FROM analysis_results"
                )
                row = cur.fetchone()
                if row and row["latest"]:
                    last_analysis = row["latest"].isoformat()

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
