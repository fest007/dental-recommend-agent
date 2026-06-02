"""
Scheduler management router.

Provides endpoints to query scheduler status and manually trigger jobs.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/status", summary="Get scheduler status")
async def get_status() -> dict[str, Any]:
    """Return the current scheduler status and list of scheduled jobs."""
    from scheduler import get_scheduler_status
    return get_scheduler_status()


@router.post("/trigger/{job_id}", summary="Manually trigger a scheduled job")
async def trigger_job_endpoint(job_id: str) -> dict[str, Any]:
    """Manually trigger a specific scheduled job.

    Available job IDs:
    - daily_enrichment: 每日商品增强增量更新
    - daily_profile_update: 每日用户画像更新
    - daily_recommendation: 每日推荐结果预计算
    - weekly_optimization: 每周反馈优化分析
    - build_knowledge_graph: 知识图谱LLM自动构建
    """
    from scheduler import trigger_job

    result = await trigger_job(job_id)
    if result.get("error") and result.get("status") != "failed":
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/build-knowledge-graph", summary="Build knowledge graph with LLM inference")
async def build_knowledge_graph_endpoint() -> dict[str, Any]:
    """Trigger LLM-based knowledge graph construction.

    Uses LLM to analyze product names and infer treatment workflow relations,
    then saves discovered relations to the product_relations table.
    """
    from db.database import async_session
    from services.knowledge_graph import build_relations_with_llm

    async with async_session() as db:
        result = await build_relations_with_llm(db)
    return result


@router.get("/checkpoints", summary="List batch checkpoints")
async def list_checkpoints(
    batch_type: str = None,
    status: str = None,
) -> dict[str, Any]:
    """List batch processing checkpoints, optionally filtered by type and status."""
    from db.database import async_session
    from sqlalchemy import select
    from db.models import BatchCheckpoint

    async with async_session() as db:
        stmt = select(BatchCheckpoint).order_by(BatchCheckpoint.created_at.desc()).limit(50)
        if batch_type:
            stmt = stmt.where(BatchCheckpoint.batch_type == batch_type)
        if status:
            stmt = stmt.where(BatchCheckpoint.status == status)

        result = await db.execute(stmt)
        rows = result.scalars().all()

        items = [
            {
                "id": row.id,
                "batch_type": row.batch_type,
                "batch_id": row.batch_id,
                "completed_keys_count": len((row.metadata_json or {}).get("completed_keys", [])),
                "total_items": row.total_items,
                "completed_items": row.completed_items,
                "failed_items": row.failed_items,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]

    return {"total": len(items), "items": items}
