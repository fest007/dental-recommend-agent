"""
APScheduler integration for daily scheduled tasks (tech_design.md section 13.3).

Three daily cron jobs:
- daily_enrichment  — 02:00 — incremental product LLM enrichment
- daily_profile_update — 03:00 — user profile recomputation
- daily_recommendation — 04:00 — pre-computed recommendations for all users
"""

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

logger = logging.getLogger(__name__)

# Module-level scheduler instance
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


# ---------------------------------------------------------------------------
# Job listeners
# ---------------------------------------------------------------------------

def _job_listener(event):
    """Log job execution results."""
    if event.exception:
        logger.error("Scheduled job '%s' failed: %s", event.job_id, event.exception)
    else:
        logger.info("Scheduled job '%s' completed successfully.", event.job_id)


# ---------------------------------------------------------------------------
# Job implementations
# ---------------------------------------------------------------------------

async def job_daily_enrichment():
    """Daily incremental product LLM enrichment (02:00).

    Finds products that have been imported but not yet enriched,
    then runs the LLM enrichment pipeline on them.
    Also detects modified products (raw updated_at > enriched_at) and re-enriches them.
    Supports checkpoint-based resume for interrupted batches.
    """
    import asyncio
    logger.info("[Scheduler] Starting daily enrichment job...")
    from db.database import async_session
    from sqlalchemy import select, or_
    from db.models import ProductRaw, ProductEnriched
    from services.checkpoint import (
        generate_batch_id, save_checkpoint, complete_checkpoint,
        load_latest_running_checkpoint,
    )

    async with async_session() as db:
        from services import llm_config_service
        from services.enrichment_graph import enrich_single_product
        from openai import OpenAI

        config = await llm_config_service.get_config()
        if not config.get("api_key"):
            logger.warning("[Scheduler] No LLM API key configured; skipping enrichment.")
            return {"enriched": 0, "error": "no_api_key"}

        sync_client = OpenAI(base_url=config["base_url"], api_key=config["api_key"])
        model = config.get("enrichment_model", "gpt-4o-mini")
        embedding_model = config.get("embedding_model", "text-embedding-3-small")

        # Check for an interrupted batch to resume
        checkpoint = await load_latest_running_checkpoint(db, "enrichment")
        completed_keys: set = set()
        batch_id = ""
        if checkpoint:
            completed_keys = checkpoint["completed_keys"]
            batch_id = checkpoint["batch_id"]
            logger.info(
                "[Scheduler] Resuming enrichment from checkpoint: batch=%s, %d items already done",
                batch_id, len(completed_keys),
            )

        # 1) Find products without enriched records (new products)
        enriched_ids_result = await db.execute(select(ProductEnriched.product_id))
        enriched_ids = {row[0] for row in enriched_ids_result.all()}

        new_result = await db.execute(
            select(ProductRaw).where(
                ProductRaw.status.notin_(["deleted", "说明SKU"]),
                ProductRaw.id.notin_(enriched_ids) if enriched_ids else True,
            ).limit(80)
        )
        new_products = list(new_result.scalars().all())

        # 2) Find products where raw was updated after enrichment (modified products)
        modified_result = await db.execute(
            select(ProductRaw).join(ProductEnriched, ProductEnriched.product_id == ProductRaw.id)
            .where(
                ProductRaw.status.notin_(["deleted", "说明SKU"]),
                ProductRaw.updated_at > ProductEnriched.enriched_at,
            ).limit(20)
        )
        modified_products = list(modified_result.scalars().all())

        # Merge: new + modified (deduplicated)
        seen_ids = set()
        products = []
        for p in new_products + modified_products:
            if p.id not in seen_ids:
                seen_ids.add(p.id)
                products.append(p)

        if not products:
            if checkpoint:
                await complete_checkpoint(
                    db=db, batch_type="enrichment", batch_id=batch_id,
                    completed_keys=list(completed_keys),
                    completed_items=checkpoint["completed_items"],
                    failed_items=checkpoint["failed_items"],
                    status="completed",
                )
            logger.info("[Scheduler] No new or modified products to enrich.")
            return {"enriched": 0, "skipped": 0}

        # Filter out already-completed products by ID
        remaining = [p for p in products if p.id not in completed_keys]
        logger.info("[Scheduler] Found %d products to enrich (%d already done).", len(remaining), len(completed_keys))

        if not remaining:
            if checkpoint:
                await complete_checkpoint(
                    db=db, batch_type="enrichment", batch_id=batch_id,
                    completed_keys=list(completed_keys),
                    completed_items=checkpoint["completed_items"],
                    failed_items=checkpoint["failed_items"],
                    status="completed",
                )
            return {"enriched": 0, "skipped": len(completed_keys)}

        if not batch_id:
            batch_id = generate_batch_id("enrichment")

        enriched_count = checkpoint["completed_items"] if checkpoint else 0
        failed_count = checkpoint["failed_items"] if checkpoint else 0
        done_keys = list(completed_keys)

        for product in remaining:
            result = await enrich_single_product(
                product_id=product.id,
                sku=product.sku,
                product_name=product.product_name,
                sync_client=sync_client,
                llm_model=model,
                embedding_model=embedding_model,
                temperature=config.get("temperature", 0.7),
                max_tokens=config.get("max_tokens", 4096),
                timeout=config.get("timeout", 30),
            )
            if result["success"]:
                enriched_count += 1
            else:
                failed_count += 1

            done_keys.append(product.id)

            if len(done_keys) % 10 == 0:
                await save_checkpoint(
                    db=db, batch_type="enrichment", batch_id=batch_id,
                    completed_keys=done_keys, total_items=len(remaining),
                    completed_items=enriched_count, failed_items=failed_count,
                    status="running",
                )

        await db.commit()

        await complete_checkpoint(
            db=db, batch_type="enrichment", batch_id=batch_id,
            completed_keys=done_keys, completed_items=enriched_count,
            failed_items=failed_count, status="completed",
        )

        logger.info("[Scheduler] Daily enrichment complete: %d enriched, %d failed", enriched_count, failed_count)
        return {"enriched": enriched_count, "failed": failed_count}


async def job_daily_profile_update():
    """Daily user profile recomputation (03:00).

    Recomputes profiles for all users who have purchase records.
    """
    logger.info("[Scheduler] Starting daily profile update job...")
    from db.database import async_session
    from sqlalchemy import select
    from db.models import UserPurchase
    from services.user_profile import compute_profile

    async with async_session() as db:
        # Get distinct user_ids
        stmt = select(UserPurchase.user_id).distinct()
        result = await db.execute(stmt)
        user_ids = [row[0] for row in result.all()]

        if not user_ids:
            logger.info("[Scheduler] No users to update.")
            return {"updated": 0}

        updated = 0
        errors = 0
        for uid in user_ids:
            try:
                await compute_profile(uid, db)
                updated += 1
            except Exception as exc:
                logger.error("[Scheduler] Profile update failed for %s: %s", uid, exc)
                errors += 1

        logger.info("[Scheduler] Profile update complete: %d updated, %d errors", updated, errors)
        return {"updated": updated, "errors": errors}


async def job_daily_recommendation():
    """Daily pre-computed recommendations for all users (04:00).

    Generates recommendations for every user via LangGraph pipeline.
    Supports checkpoint-based resume for interrupted batches.
    """
    logger.info("[Scheduler] Starting daily recommendation job...")
    from db.database import async_session
    from sqlalchemy import select
    from db.models import UserPurchase
    from services.llm_config_service import get_client, get_config
    from services.recommendation_graph import run_recommendation_graph
    from services.checkpoint import (
        generate_batch_id, save_checkpoint, complete_checkpoint,
        load_latest_running_checkpoint,
    )

    async with async_session() as db:
        stmt = select(UserPurchase.user_id).distinct()
        result = await db.execute(stmt)
        user_ids = [row[0] for row in result.all()]

        if not user_ids:
            logger.info("[Scheduler] No users to generate recommendations for.")
            return {"generated": 0, "errors": 0}

        # Check for interrupted batch
        checkpoint = await load_latest_running_checkpoint(db, "recommendation")
        completed_keys: set = set()
        batch_id = ""
        if checkpoint:
            completed_keys = checkpoint["completed_keys"]
            batch_id = checkpoint["batch_id"]
            logger.info("[Scheduler] Resuming recommendation: batch=%s, %d users already done", batch_id, len(completed_keys))

        # Filter out already-completed users
        remaining = [uid for uid in user_ids if uid not in completed_keys]
        if not remaining:
            if checkpoint:
                await complete_checkpoint(
                    db=db, batch_type="recommendation", batch_id=batch_id,
                    completed_keys=list(completed_keys),
                    completed_items=checkpoint["completed_items"],
                    failed_items=checkpoint["failed_items"], status="completed",
                )
            logger.info("[Scheduler] All users already processed.")
            return {"generated": 0, "skipped": len(completed_keys)}

        if not batch_id:
            batch_id = generate_batch_id("recommendation")

        client = await get_client()
        config = await get_config()
        generated = checkpoint["completed_items"] if checkpoint else 0
        errors = checkpoint["failed_items"] if checkpoint else 0
        done_keys = list(completed_keys)

        for uid in remaining:
            try:
                recs = await run_recommendation_graph(uid, db, client, config)
                generated += 1
                logger.info("[Scheduler] Generated %d recs for user %s", len(recs), uid)
            except Exception as exc:
                await db.rollback()
                logger.error("[Scheduler] Recommendation failed for %s: %s", uid, exc)
                errors += 1

            done_keys.append(uid)

            if len(done_keys) % 5 == 0:
                await save_checkpoint(
                    db=db, batch_type="recommendation", batch_id=batch_id,
                    completed_keys=done_keys, total_items=len(remaining),
                    completed_items=generated, failed_items=errors,
                    status="running",
                )

        await db.commit()

        await complete_checkpoint(
            db=db, batch_type="recommendation", batch_id=batch_id,
            completed_keys=done_keys, completed_items=generated,
            failed_items=errors, status="completed",
        )

        logger.info("[Scheduler] Daily recommendation complete: %d users, %d errors", generated, errors)
        return {"generated": generated, "errors": errors}


async def job_weekly_optimization():
    """Weekly feedback-driven optimization (Monday 05:00).

    Runs the feedback analysis and prompt optimization cycle.
    """
    logger.info("[Scheduler] Starting weekly optimization job...")
    from db.database import async_session
    from services.feedback_optimizer import run_weekly_optimization

    async with async_session() as db:
        result = await run_weekly_optimization(db)
        logger.info("[Scheduler] Weekly optimization complete: %s", result.get("summary", ""))
        return result


async def job_build_knowledge_graph():
    """Build knowledge graph with LLM inference (manual trigger).

    Uses LLM to analyze product names and infer treatment workflow relations.
    """
    logger.info("[Scheduler] Starting knowledge graph construction...")
    from db.database import async_session
    from services.knowledge_graph import build_relations_with_llm

    async with async_session() as db:
        result = await build_relations_with_llm(db)
        logger.info("[Scheduler] Knowledge graph construction complete: %s", result)
        return result


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def start_scheduler():
    """Configure and start the APScheduler."""
    if scheduler.running:
        logger.warning("Scheduler is already running.")
        return

    scheduler.add_listener(_job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    # Daily enrichment at 02:00
    scheduler.add_job(
        job_daily_enrichment,
        trigger=CronTrigger(hour=2, minute=0),
        id="daily_enrichment",
        name="每日商品增强增量更新",
        replace_existing=True,
    )

    # Daily profile update at 03:00
    scheduler.add_job(
        job_daily_profile_update,
        trigger=CronTrigger(hour=3, minute=0),
        id="daily_profile_update",
        name="每日用户画像更新",
        replace_existing=True,
    )

    # Daily recommendation at 04:00
    scheduler.add_job(
        job_daily_recommendation,
        trigger=CronTrigger(hour=4, minute=0),
        id="daily_recommendation",
        name="每日推荐结果预计算",
        replace_existing=True,
    )

    # Weekly optimization on Monday at 05:00
    scheduler.add_job(
        job_weekly_optimization,
        trigger=CronTrigger(day_of_week="mon", hour=5, minute=0),
        id="weekly_optimization",
        name="每周反馈优化分析",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started with %d jobs.", len(scheduler.get_jobs()))


def stop_scheduler():
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


def get_scheduler_status() -> dict:
    """Return current scheduler status and job list."""
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run.isoformat() if next_run else None,
            "trigger": str(job.trigger),
        })

    return {
        "running": scheduler.running,
        "jobs": jobs,
        "job_count": len(jobs),
    }


async def trigger_job(job_id: str) -> dict:
    """Manually trigger a scheduled job by its ID."""
    job_map = {
        "daily_enrichment": job_daily_enrichment,
        "daily_profile_update": job_daily_profile_update,
        "daily_recommendation": job_daily_recommendation,
        "weekly_optimization": job_weekly_optimization,
        "build_knowledge_graph": job_build_knowledge_graph,
    }

    func = job_map.get(job_id)
    if not func:
        return {"error": f"Unknown job: {job_id}", "available": list(job_map.keys())}

    logger.info("[Scheduler] Manually triggering job: %s", job_id)
    try:
        result = await func()
        return {"job_id": job_id, "status": "completed", "result": result}
    except Exception as exc:
        logger.error("[Scheduler] Manual trigger of '%s' failed: %s", job_id, exc)
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
