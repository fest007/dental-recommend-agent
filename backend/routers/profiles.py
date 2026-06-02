"""
User profiles router.

Provides CRUD and computation endpoints for user profiles.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import UserProfile

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ProfileUpdate(BaseModel):
    """Partial update for a user profile's JSON fields."""
    basic_info: Optional[dict] = None
    category_preference: Optional[list] = None
    brand_preference: Optional[list] = None
    purchase_cycle: Optional[dict] = None
    consumable_alerts: Optional[list] = None
    recency_score: Optional[float] = None
    value_tier: Optional[str] = None


class ProfileOut(BaseModel):
    id: int
    user_id: str
    profile_json: dict
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", summary="List user profiles with pagination")
async def list_profiles(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return a paginated list of user profiles."""
    # Total count
    count_stmt = select(sa_func.count()).select_from(UserProfile)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Paginated query
    offset = (page - 1) * page_size
    stmt = (
        select(UserProfile)
        .order_by(UserProfile.user_id)
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    items = [
        {
            "id": row.id,
            "user_id": row.user_id,
            "profile_json": row.profile_json or {},
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@router.get("/{user_id}", summary="Get a single user profile")
async def get_profile(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the profile for the given user_id."""
    user_id = user_id.strip().upper()
    stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Profile not found for user {user_id}")

    return {
        "id": row.id,
        "user_id": row.user_id,
        "profile_json": row.profile_json or {},
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.post("/compute/{user_id}", summary="Trigger profile computation")
async def compute_profile_endpoint(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Compute (or recompute) the user profile from purchase history."""
    user_id = user_id.strip().upper()
    from services.user_profile import compute_profile

    profile = await compute_profile(user_id, db)
    return {
        "message": f"Profile computed for user {user_id}",
        "profile": profile,
    }


@router.put("/{user_id}", summary="Update profile fields")
async def update_profile(
    user_id: str,
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Partially update the profile_json for the given user.

    Only the fields provided in the request body will be merged into the
    existing profile JSON; other fields are left untouched.
    """
    user_id = user_id.strip().upper()
    stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Profile not found for user {user_id}")

    current: dict = row.profile_json or {}

    # Merge only provided fields
    update_data = body.model_dump(exclude_none=True)
    for key, value in update_data.items():
        current[key] = value

    row.profile_json = current
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)

    return {
        "id": row.id,
        "user_id": row.user_id,
        "profile_json": row.profile_json,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
