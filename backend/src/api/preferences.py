"""FastAPI router for user preferences endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field, field_validator
from psycopg2.extras import RealDictCursor

import structlog

from backend.src.db.connection import get_db_connection
from backend.src.models.schemas import (
    CompanySize,
    RiskLevel,
    VALID_SECTORS,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


# --- Auth Dependency ---


async def get_current_user_id(x_user_id: Optional[str] = Header(None)) -> UUID:
    """Extract user_id from request header.

    This is a placeholder dependency that will be replaced with Cognito JWT
    validation later. For now, it reads user_id from the X-User-Id header.
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user identity")


# --- Request/Response Models ---


class PreferencesResponse(BaseModel):
    """Response model for user preferences."""

    preferred_sectors: list[str] = Field(default_factory=list)
    preferred_sizes: list[str] = Field(default_factory=list)
    max_risk_level: str = Field(default="HIGH")


class UpdatePreferencesRequest(BaseModel):
    """Request body for updating user preferences."""

    preferred_sectors: list[str] = Field(default_factory=list)
    preferred_sizes: list[str] = Field(default_factory=list)
    max_risk_level: str = Field(default="HIGH")

    @field_validator("preferred_sectors")
    @classmethod
    def validate_sectors(cls, v: list[str]) -> list[str]:
        for sector in v:
            if sector not in VALID_SECTORS:
                raise ValueError(
                    f"Invalid sector '{sector}'. Must be one of: {', '.join(VALID_SECTORS)}"
                )
        return v

    @field_validator("preferred_sizes")
    @classmethod
    def validate_sizes(cls, v: list[str]) -> list[str]:
        valid_sizes = [s.value for s in CompanySize]
        for size in v:
            if size not in valid_sizes:
                raise ValueError(
                    f"Invalid size '{size}'. Must be one of: {', '.join(valid_sizes)}"
                )
        return v

    @field_validator("max_risk_level")
    @classmethod
    def validate_risk_level(cls, v: str) -> str:
        valid_levels = [r.value for r in RiskLevel]
        if v not in valid_levels:
            raise ValueError(
                f"Invalid risk level '{v}'. Must be one of: {', '.join(valid_levels)}"
            )
        return v


# --- Endpoints ---


@router.get("", response_model=PreferencesResponse)
async def get_preferences(user_id: UUID = Depends(get_current_user_id)):
    """Get the preferences for the authenticated user.

    Returns default values if no preferences have been stored.
    """
    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT preferred_sectors, preferred_sizes, max_risk_level
                FROM user_preferences
                WHERE user_id = %s
                """,
                (str(user_id),),
            )
            row = cur.fetchone()

            if not row:
                return PreferencesResponse(
                    preferred_sectors=[],
                    preferred_sizes=[],
                    max_risk_level="HIGH",
                )

            return PreferencesResponse(
                preferred_sectors=row["preferred_sectors"] or [],
                preferred_sizes=row["preferred_sizes"] or [],
                max_risk_level=row["max_risk_level"] or "HIGH",
            )


@router.put("", response_model=PreferencesResponse)
async def update_preferences(
    request: UpdatePreferencesRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    """Update the preferences for the authenticated user.

    Creates preferences if they don't exist, updates if they do.
    """
    async with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO user_preferences (user_id, preferred_sectors, preferred_sizes, max_risk_level, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    preferred_sectors = EXCLUDED.preferred_sectors,
                    preferred_sizes = EXCLUDED.preferred_sizes,
                    max_risk_level = EXCLUDED.max_risk_level,
                    updated_at = NOW()
                RETURNING preferred_sectors, preferred_sizes, max_risk_level
                """,
                (
                    str(user_id),
                    request.preferred_sectors,
                    request.preferred_sizes,
                    request.max_risk_level,
                ),
            )
            row = cur.fetchone()

            logger.info(
                "User preferences updated",
                user_id=str(user_id),
                sectors=request.preferred_sectors,
                sizes=request.preferred_sizes,
                max_risk_level=request.max_risk_level,
            )

            return PreferencesResponse(
                preferred_sectors=row["preferred_sectors"] or [],
                preferred_sizes=row["preferred_sizes"] or [],
                max_risk_level=row["max_risk_level"] or "HIGH",
            )
