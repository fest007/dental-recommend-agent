"""
Recommendations router.

Provides endpoints to list, generate, and provide feedback on
user recommendations.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import FeedbackLog, ProductRaw, Recommendation, UserPurchase

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class FeedbackBody(BaseModel):
    status: str  # accepted / rejected / modified
    feedback_note: Optional[str] = ""
    modification: Optional[str] = ""  # modified状态时记录修改内容


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", summary="List recommendations with optional user filter")
async def list_recommendations(
    user_id: Optional[str] = Query(None, description="Filter by user_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return a paginated list of recommendations, optionally filtered by user."""
    # Build base query with product name join
    base = (
        select(Recommendation, ProductRaw.product_name)
        .outerjoin(ProductRaw, Recommendation.recommended_sku == ProductRaw.sku)
    )
    count_base = select(sa_func.count()).select_from(Recommendation)

    if user_id:
        user_id = user_id.strip().upper()
        base = base.where(Recommendation.user_id == user_id)
        count_base = count_base.where(Recommendation.user_id == user_id)

    # Total count
    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    # Paginated data
    offset = (page - 1) * page_size
    stmt = base.order_by(Recommendation.user_id, Recommendation.rank).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.all()

    items = [
        {
            "id": rec.id,
            "user_id": rec.user_id,
            "recommended_sku": rec.recommended_sku,
            "product_name": product_name or "",
            "rank": rec.rank,
            "reason": rec.reason,
            "confidence": rec.confidence,
            "source": rec.source,
            "status": rec.status,
            "feedback_at": rec.feedback_at.isoformat() if rec.feedback_at else None,
            "generated_at": rec.generated_at.isoformat() if rec.generated_at else None,
        }
        for rec, product_name in rows
    ]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@router.get("/{user_id}", summary="Get recommendations for a user")
async def get_recommendations(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return all recommendations for the given user, ordered by rank."""
    user_id = user_id.strip().upper()
    stmt = (
        select(Recommendation, ProductRaw.product_name)
        .outerjoin(ProductRaw, Recommendation.recommended_sku == ProductRaw.sku)
        .where(Recommendation.user_id == user_id)
        .order_by(Recommendation.rank)
    )
    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return {"user_id": user_id, "items": [], "message": "No recommendations found"}

    items = [
        {
            "id": rec.id,
            "user_id": rec.user_id,
            "recommended_sku": rec.recommended_sku,
            "product_name": product_name or "",
            "rank": rec.rank,
            "reason": rec.reason,
            "confidence": rec.confidence,
            "source": rec.source,
            "status": rec.status,
            "feedback_at": rec.feedback_at.isoformat() if rec.feedback_at else None,
            "generated_at": rec.generated_at.isoformat() if rec.generated_at else None,
        }
        for rec, product_name in rows
    ]

    return {"user_id": user_id, "items": items}


@router.post("/generate/{user_id}", summary="Generate recommendations for one user")
async def generate_for_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Trigger recommendation generation via LangGraph pipeline.

    The graph runs: load_context -> parallel recall (graph/vector/rule/kg)
    -> merge -> llm_rank -> store. All steps traced by LangSmith.
    """
    user_id = user_id.strip().upper()
    from services import llm_config_service
    from services.recommendation_graph import run_recommendation_graph

    # Verify user has purchases
    purchase_stmt = select(sa_func.count()).select_from(UserPurchase).where(
        UserPurchase.user_id == user_id
    )
    count_result = await db.execute(purchase_stmt)
    count = count_result.scalar() or 0

    if count == 0:
        # Allow cold-start (hot product fallback)
        pass

    client = await llm_config_service.get_client()
    config = await llm_config_service.get_config()
    results = await run_recommendation_graph(user_id, db, client, config)

    return {
        "message": f"Generated {len(results)} recommendations for user {user_id}",
        "items": results,
    }


@router.post("/generate-all", summary="Generate recommendations for all users")
async def generate_for_all(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Trigger recommendation generation for every user via LangGraph."""
    from services import llm_config_service
    from services.recommendation_graph import run_recommendation_graph

    # Get distinct user_ids from purchases
    stmt = select(UserPurchase.user_id).distinct()
    result = await db.execute(stmt)
    user_ids = [row[0] for row in result.all()]

    if not user_ids:
        return {"message": "No users with purchase records", "results": []}

    client = await llm_config_service.get_client()
    config = await llm_config_service.get_config()

    all_results: list[dict] = []
    errors: list[dict] = []

    for uid in user_ids:
        try:
            recs = await run_recommendation_graph(uid, db, client, config)
            all_results.append({"user_id": uid, "count": len(recs)})
        except Exception as exc:
            await db.rollback()
            logger.error("Failed to generate recommendations for %s: %s", uid, exc)
            errors.append({"user_id": uid, "error": str(exc)})

    return {
        "message": f"Processed {len(user_ids)} users",
        "results": all_results,
        "errors": errors,
    }


@router.get("/feedback-stats/summary", summary="Get feedback adoption statistics")
async def get_feedback_stats(
    days: int = Query(30, ge=1, le=365, description="Statistics period in days"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return adoption rate statistics per recall source."""
    from services.feedback_optimizer import compute_source_adoption_rates

    stats = await compute_source_adoption_rates(db, days=days)
    return stats


@router.get("/feedback-stats/failures", summary="Get failed recommendation analysis")
async def get_feedback_failures(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Analyze rejected recommendations to find failure patterns."""
    from services.feedback_optimizer import analyze_failed_recommendations

    patterns = await analyze_failed_recommendations(db, days=days)
    return {"period_days": days, "patterns": patterns}


@router.post("/optimize", summary="Run feedback-driven optimization")
async def run_optimization(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Trigger the weekly feedback optimization cycle manually."""
    from services.feedback_optimizer import run_weekly_optimization

    result = await run_weekly_optimization(db)
    return result


@router.put("/{id}/feedback", summary="Update recommendation feedback")
async def update_feedback(
    id: int,
    body: FeedbackBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update the status of a recommendation (accepted/rejected/modified) and log
    the feedback event.
    """
    if body.status not in ("accepted", "rejected", "modified"):
        raise HTTPException(
            status_code=400,
            detail="status must be 'accepted', 'rejected', or 'modified'",
        )

    # Find the recommendation
    stmt = select(Recommendation).where(Recommendation.id == id)
    result = await db.execute(stmt)
    rec = result.scalar_one_or_none()

    if rec is None:
        raise HTTPException(status_code=404, detail=f"Recommendation {id} not found")

    # Update recommendation status
    rec.status = body.status
    rec.feedback_at = datetime.now(timezone.utc)

    # Create feedback log entry
    note = body.feedback_note or ""
    if body.status == "modified" and body.modification:
        note = f"{note} | 修改内容: {body.modification}" if note else f"修改内容: {body.modification}"

    feedback = FeedbackLog(
        recommendation_id=rec.id,
        user_id=rec.user_id,
        action=body.status,
        feedback_note=note,
    )
    db.add(feedback)

    await db.commit()
    await db.refresh(rec)

    return {
        "id": rec.id,
        "user_id": rec.user_id,
        "recommended_sku": rec.recommended_sku,
        "status": rec.status,
        "feedback_at": rec.feedback_at.isoformat() if rec.feedback_at else None,
        "message": f"Recommendation marked as {body.status}",
    }
