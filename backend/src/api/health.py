"""FastAPI router for health check endpoint."""

from datetime import datetime, timezone
import os
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

import structlog

from src.db.connection import store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

STOCK_FRESHNESS_SLA_HOURS = int(os.environ.get("HEALTH_STOCK_FRESHNESS_SLA_HOURS", "36"))
NEWS_FRESHNESS_SLA_HOURS = int(os.environ.get("HEALTH_NEWS_FRESHNESS_SLA_HOURS", "2"))
CALENDAR_FRESHNESS_SLA_HOURS = int(
    os.environ.get("HEALTH_CALENDAR_FRESHNESS_SLA_HOURS", "36")
)
ANALYSIS_FRESHNESS_SLA_HOURS = int(
    os.environ.get("HEALTH_ANALYSIS_FRESHNESS_SLA_HOURS", "36")
)
PUBLICATION_FRESHNESS_SLA_HOURS = int(
    os.environ.get("HEALTH_PUBLICATION_FRESHNESS_SLA_HOURS", "36")
)


def _optional_string(value: object) -> Optional[str]:
    return value if isinstance(value, str) else None


class ComponentStatus(BaseModel):
    """Status of individual system components."""

    database: str
    stock_collection: str = "unknown"
    news_collection: str = "unknown"
    earnings_collection: str = "unknown"
    dividend_collection: str = "unknown"
    analysis: str = "unknown"
    publication: str = "unknown"


class FreshnessStatus(BaseModel):
    """Freshness details for a timestamped pipeline component."""

    status: str
    last_success_at: Optional[str] = None
    age_hours: Optional[float] = None
    max_age_hours: int
    reason: Optional[str] = None


class HealthResponse(BaseModel):
    """Response model for the health check endpoint."""

    status: str
    components: ComponentStatus
    freshness: dict[str, FreshnessStatus] = Field(default_factory=dict)
    last_stock_collection: Optional[str] = None
    last_news_collection: Optional[str] = None
    last_earnings_collection: Optional[str] = None
    last_dividend_collection: Optional[str] = None
    last_stock_collection_summary: Optional[dict[str, Any]] = None
    last_news_collection_summary: Optional[dict[str, Any]] = None
    last_analysis: Optional[str] = None
    last_publication: Optional[str] = None


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Return system health status including component states and last batch timestamps.

    No authentication required. Returns the operational status of all components
    and external dependencies.
    """
    db_status = "ok"
    last_stock_collection: Optional[str] = None
    last_news_collection: Optional[str] = None
    last_earnings_collection: Optional[str] = None
    last_dividend_collection: Optional[str] = None
    last_stock_collection_summary: dict[str, Any] | None = None
    last_news_collection_summary: dict[str, Any] | None = None
    last_analysis: Optional[str] = None
    last_publication: Optional[str] = None

    try:
        store.ping()
        last_stock_collection = _optional_string(store.last_stock_collection())
        last_news_collection = _optional_string(store.last_news_collection())
        last_earnings_collection = _optional_string(store.last_earnings_collection())
        last_dividend_collection = _optional_string(store.last_dividend_collection())
        last_stock_collection_summary = store.last_stock_collection_summary()
        last_news_collection_summary = store.last_news_collection_summary()
        last_analysis = _optional_string(store.last_analysis())
        last_publication = _optional_string(store.last_publication())

    except Exception as e:
        db_status = "error"
        logger.error("Health check database error", error=str(e))

    freshness = {
        "stock_collection": _freshness_status(
            last_stock_collection, STOCK_FRESHNESS_SLA_HOURS
        ),
        "news_collection": _freshness_status(
            last_news_collection, NEWS_FRESHNESS_SLA_HOURS
        ),
        "earnings_collection": _freshness_status(
            last_earnings_collection, CALENDAR_FRESHNESS_SLA_HOURS
        ),
        "dividend_collection": _freshness_status(
            last_dividend_collection, CALENDAR_FRESHNESS_SLA_HOURS
        ),
        "analysis": _freshness_status(last_analysis, ANALYSIS_FRESHNESS_SLA_HOURS),
        "publication": _freshness_status(
            last_publication, PUBLICATION_FRESHNESS_SLA_HOURS
        ),
    }
    component_status = ComponentStatus(
        database=db_status,
        stock_collection=freshness["stock_collection"].status,
        news_collection=freshness["news_collection"].status,
        earnings_collection=freshness["earnings_collection"].status,
        dividend_collection=freshness["dividend_collection"].status,
        analysis=freshness["analysis"].status,
        publication=freshness["publication"].status,
    )
    overall_status = (
        "ok"
        if db_status == "ok"
        and all(item.status == "ok" for item in freshness.values())
        else "degraded"
    )

    return HealthResponse(
        status=overall_status,
        components=component_status,
        freshness=freshness,
        last_stock_collection=last_stock_collection,
        last_news_collection=last_news_collection,
        last_earnings_collection=last_earnings_collection,
        last_dividend_collection=last_dividend_collection,
        last_stock_collection_summary=last_stock_collection_summary,
        last_news_collection_summary=last_news_collection_summary,
        last_analysis=last_analysis,
        last_publication=last_publication,
    )


def _freshness_status(
    timestamp: str | None,
    max_age_hours: int,
    *,
    now: datetime | None = None,
) -> FreshnessStatus:
    """Evaluate whether a component timestamp is still fresh."""

    if not timestamp:
        return FreshnessStatus(
            status="missing",
            max_age_hours=max_age_hours,
            reason="No successful run has been recorded.",
        )

    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return FreshnessStatus(
            status="degraded",
            last_success_at=timestamp,
            max_age_hours=max_age_hours,
            reason="Last successful run timestamp is not parseable.",
        )

    reference_time = now or datetime.now(timezone.utc)
    age_hours = max((reference_time - parsed).total_seconds() / 3600, 0)
    rounded_age = round(age_hours, 2)
    if age_hours > max_age_hours:
        return FreshnessStatus(
            status="stale",
            last_success_at=timestamp,
            age_hours=rounded_age,
            max_age_hours=max_age_hours,
            reason=f"Last successful run is older than {max_age_hours} hours.",
        )

    return FreshnessStatus(
        status="ok",
        last_success_at=timestamp,
        age_hours=rounded_age,
        max_age_hours=max_age_hours,
    )


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
