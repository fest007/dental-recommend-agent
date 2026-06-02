"""
Batch processing checkpoint utility (tech_design.md section 10.4).

Stores completed item keys (IDs or strings) in metadata_json so that
resume works correctly even if the item list changes between runs.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BatchCheckpoint

logger = logging.getLogger(__name__)


async def save_checkpoint(
    db: AsyncSession,
    batch_type: str,
    batch_id: str,
    completed_keys: list,
    total_items: int,
    completed_items: int = 0,
    failed_items: int = 0,
    status: str = "running",
) -> None:
    """Save or update a batch checkpoint.

    Parameters
    ----------
    completed_keys : list
        List of item keys that have been processed (int IDs or string user IDs).
        Stored in metadata_json.completed_keys.
    """
    metadata = {"completed_keys": completed_keys}

    stmt = select(BatchCheckpoint).where(
        BatchCheckpoint.batch_type == batch_type,
        BatchCheckpoint.batch_id == batch_id,
    )
    result = await db.execute(stmt)
    checkpoint = result.scalar_one_or_none()

    if checkpoint:
        checkpoint.total_items = total_items
        checkpoint.completed_items = completed_items
        checkpoint.failed_items = failed_items
        checkpoint.metadata_json = metadata
        checkpoint.status = status
    else:
        checkpoint = BatchCheckpoint(
            batch_type=batch_type,
            batch_id=batch_id,
            last_completed_idx=len(completed_keys),
            total_items=total_items,
            completed_items=completed_items,
            failed_items=failed_items,
            metadata_json=metadata,
            status=status,
        )
        db.add(checkpoint)

    await db.commit()


async def load_latest_running_checkpoint(
    db: AsyncSession,
    batch_type: str,
) -> Optional[dict]:
    """Load the most recent running checkpoint for a batch type."""
    stmt = (
        select(BatchCheckpoint)
        .where(
            BatchCheckpoint.batch_type == batch_type,
            BatchCheckpoint.status == "running",
        )
        .order_by(BatchCheckpoint.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    checkpoint = result.scalar_one_or_none()

    if checkpoint is None:
        return None

    metadata = checkpoint.metadata_json or {}
    return {
        "batch_type": checkpoint.batch_type,
        "batch_id": checkpoint.batch_id,
        "completed_keys": set(metadata.get("completed_keys", [])),
        "total_items": checkpoint.total_items,
        "completed_items": checkpoint.completed_items,
        "failed_items": checkpoint.failed_items,
        "status": checkpoint.status,
        "created_at": checkpoint.created_at.isoformat() if checkpoint.created_at else None,
        "updated_at": checkpoint.updated_at.isoformat() if checkpoint.updated_at else None,
    }


async def complete_checkpoint(
    db: AsyncSession,
    batch_type: str,
    batch_id: str,
    completed_keys: list,
    completed_items: int,
    failed_items: int,
    status: str = "completed",
) -> None:
    """Mark a checkpoint as completed."""
    await save_checkpoint(
        db=db, batch_type=batch_type, batch_id=batch_id,
        completed_keys=completed_keys, total_items=completed_items + failed_items,
        completed_items=completed_items, failed_items=failed_items,
        status=status,
    )


async def cleanup_old_checkpoints(
    db: AsyncSession,
    batch_type: Optional[str] = None,
    keep_days: int = 7,
) -> int:
    """Remove checkpoints older than keep_days."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    stmt = delete(BatchCheckpoint).where(BatchCheckpoint.created_at < cutoff)
    if batch_type:
        stmt = stmt.where(BatchCheckpoint.batch_type == batch_type)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount


def generate_batch_id(batch_type: str) -> str:
    """Generate a unique batch ID for a new batch run."""
    now = datetime.now()
    return f"{batch_type}_{now.strftime('%Y%m%d_%H%M%S')}"
