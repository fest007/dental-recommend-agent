"""
Feedback-driven optimization service (tech_design.md section 11.3).

Collects recommendation feedback, computes adoption rates per recall source,
analyzes failure patterns, and dynamically adjusts ranking prompt strategies.

Optimization flowchart:
- Graph recall adoption high → increase graph recall weight hint
- Vector recall adoption low → flag embedding strategy review
- Rule recall adoption high → expand cycle reminder range
- Category-specific poor performance → optimize category prompt
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FeedbackLog, OptimizationLog, ProductEnriched, Recommendation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adoption rate statistics
# ---------------------------------------------------------------------------

async def compute_source_adoption_rates(
    db: AsyncSession,
    days: int = 30,
) -> dict[str, Any]:
    """Compute adoption (acceptance) rate for each recall source.

    Returns::

        {
            "period_days": 30,
            "total_recommendations": 120,
            "total_feedback": 80,
            "overall_adoption_rate": 0.65,
            "by_source": {
                "graph": {"total": 40, "accepted": 30, "rejected": 10, "adoption_rate": 0.75},
                "vector": {"total": 30, "accepted": 15, "rejected": 15, "adoption_rate": 0.50},
                ...
            }
        }
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get all recommendations with feedback in the period (by feedback time, not generation time)
    stmt = (
        select(Recommendation)
        .where(Recommendation.feedback_at >= cutoff)
        .where(Recommendation.status.in_(["accepted", "rejected"]))
    )
    result = await db.execute(stmt)
    recs = result.scalars().all()

    by_source: dict[str, dict] = defaultdict(lambda: {"total": 0, "accepted": 0, "rejected": 0})
    for rec in recs:
        src = rec.source or "unknown"
        by_source[src]["total"] += 1
        if rec.status == "accepted":
            by_source[src]["accepted"] += 1
        elif rec.status == "rejected":
            by_source[src]["rejected"] += 1

    # Calculate rates
    for src, stats in by_source.items():
        total = stats["total"]
        stats["adoption_rate"] = round(stats["accepted"] / total, 3) if total > 0 else 0.0

    total_recs = select(sa_func.count()).select_from(Recommendation).where(
        Recommendation.feedback_at >= cutoff
    )
    total_result = await db.execute(total_recs)
    total_count = total_result.scalar() or 0

    total_feedback = sum(s["total"] for s in by_source.values())
    total_accepted = sum(s["accepted"] for s in by_source.values())

    return {
        "period_days": days,
        "total_recommendations": total_count,
        "total_feedback": total_feedback,
        "overall_adoption_rate": round(total_accepted / total_feedback, 3) if total_feedback > 0 else 0.0,
        "by_source": dict(by_source),
    }


# ---------------------------------------------------------------------------
# Failed recommendation analysis
# ---------------------------------------------------------------------------

async def analyze_failed_recommendations(
    db: AsyncSession,
    days: int = 30,
    limit: int = 20,
) -> list[dict]:
    """Analyze rejected recommendations to find failure patterns.

    Returns list of failure patterns:
    - Which categories are frequently rejected
    - Which brands are frequently rejected
    - Which sources have high rejection rates
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get rejected recommendations (by feedback time, not generation time)
    stmt = (
        select(Recommendation)
        .where(Recommendation.feedback_at >= cutoff)
        .where(Recommendation.status == "rejected")
        .limit(200)
    )
    result = await db.execute(stmt)
    rejected = result.scalars().all()

    if not rejected:
        return []

    # Look up enriched data for rejected SKUs
    rejected_skus = list({r.recommended_sku for r in rejected if r.recommended_sku})
    enriched_stmt = select(ProductEnriched).where(ProductEnriched.sku.in_(rejected_skus))
    enriched_result = await db.execute(enriched_stmt)
    enriched_map = {e.sku: e for e in enriched_result.scalars().all()}

    # Analyze patterns
    category_rejects: Counter = Counter()
    brand_rejects: Counter = Counter()
    source_rejects: Counter = Counter()

    for rec in rejected:
        source_rejects[rec.source or "unknown"] += 1
        enriched = enriched_map.get(rec.recommended_sku)
        if enriched:
            cat = enriched.category_l1 or "未分类"
            brand = enriched.brand or "未知"
            category_rejects[cat] += 1
            brand_rejects[brand] += 1

    # Get feedback notes for rejected items
    feedback_stmt = (
        select(FeedbackLog)
        .where(FeedbackLog.created_at >= cutoff)
        .where(FeedbackLog.action == "rejected")
        .where(FeedbackLog.feedback_note != "")
        .limit(limit)
    )
    feedback_result = await db.execute(feedback_stmt)
    feedback_notes = [
        {
            "recommendation_id": f.recommendation_id,
            "user_id": f.user_id,
            "note": f.feedback_note,
        }
        for f in feedback_result.scalars().all()
    ]

    patterns: list[dict] = []

    if category_rejects:
        top_cat, top_count = category_rejects.most_common(1)[0]
        patterns.append({
            "type": "category_rejection",
            "description": f"品类「{top_cat}」被拒绝{top_count}次，推荐质量需优化",
            "detail": dict(category_rejects.most_common(5)),
            "suggestion": "优化该品类的LLM排序Prompt或调整召回策略",
        })

    if brand_rejects:
        top_brand, top_count = brand_rejects.most_common(1)[0]
        if top_count >= 3:
            patterns.append({
                "type": "brand_rejection",
                "description": f"品牌「{top_brand}」被拒绝{top_count}次",
                "detail": dict(brand_rejects.most_common(5)),
                "suggestion": "检查该品牌商品的推荐理由是否准确",
            })

    if source_rejects:
        patterns.append({
            "type": "source_rejection",
            "description": "各召回源被拒绝统计",
            "detail": dict(source_rejects.most_common()),
            "suggestion": "关注高拒绝率的召回源，调整权重",
        })

    if feedback_notes:
        patterns.append({
            "type": "user_feedback_notes",
            "description": f"收到{len(feedback_notes)}条用户反馈备注",
            "detail": feedback_notes,
            "suggestion": "分析用户反馈备注，优化推荐理由生成",
        })

    return patterns


# ---------------------------------------------------------------------------
# Dynamic prompt adjustment
# ---------------------------------------------------------------------------

async def compute_prompt_adjustments(
    source_stats: dict[str, Any],
    failed_patterns: list[dict],
) -> dict[str, Any]:
    """Compute ranking prompt adjustments based on feedback analysis.

    Returns adjustments that should be injected into the ranking prompt:
    - source_weight_hints: which recall sources to emphasize
    - category_penalties: categories to be cautious about
    - strategy_notes: text hints for the LLM
    """
    by_source = source_stats.get("by_source", {})

    # Determine which sources are performing well / poorly
    source_weight_hints: dict[str, str] = {}
    for src, stats in by_source.items():
        rate = stats.get("adoption_rate", 0)
        total = stats.get("total", 0)
        if total < 5:
            continue  # not enough data
        if rate >= 0.7:
            source_weight_hints[src] = "high"
        elif rate <= 0.3:
            source_weight_hints[src] = "low"
        else:
            source_weight_hints[src] = "medium"

    # Category penalties from failure analysis
    category_penalties: list[str] = []
    for pattern in failed_patterns:
        if pattern.get("type") == "category_rejection":
            detail = pattern.get("detail", {})
            for cat, count in detail.items():
                if count >= 5:
                    category_penalties.append(cat)

    # Build strategy notes
    strategy_notes: list[str] = []

    if source_weight_hints.get("graph") == "high":
        strategy_notes.append("图召回采纳率高，优先考虑基于商品关系的推荐理由")
    if source_weight_hints.get("vector") == "low":
        strategy_notes.append("向量召回采纳率低，推荐理由需更具体，避免泛泛的语义相似")
    if source_weight_hints.get("rule") == "high":
        strategy_notes.append("规则召回（周期补货）采纳率高，重视补货类推荐")
    if source_weight_hints.get("knowledge_graph") == "high":
        strategy_notes.append("知识图谱召回采纳率高，优先推荐治疗流程相关产品")

    if category_penalties:
        cats = "、".join(category_penalties[:3])
        strategy_notes.append(f"以下品类近期被拒绝较多，推荐时需格外谨慎：{cats}")

    return {
        "source_weight_hints": source_weight_hints,
        "category_penalties": category_penalties,
        "strategy_notes": strategy_notes,
    }


# ---------------------------------------------------------------------------
# Ranking prompt builder with dynamic adjustments
# ---------------------------------------------------------------------------

async def build_dynamic_ranking_context(db: AsyncSession) -> str:
    """Build additional context text to inject into the ranking prompt.

    This text is prepended to the candidates section of the ranking prompt,
    giving the LLM feedback-driven hints.
    """
    try:
        stats = await compute_source_adoption_rates(db, days=30)
        patterns = await analyze_failed_recommendations(db, days=30)
        adjustments = await compute_prompt_adjustments(stats, patterns)
    except Exception as exc:
        logger.warning("Failed to compute prompt adjustments: %s", exc)
        return ""

    notes = adjustments.get("strategy_notes", [])
    if not notes:
        return ""

    lines = ["## 历史反馈驱动的优化提示"]
    for note in notes:
        lines.append(f"- {note}")

    overall_rate = stats.get("overall_adoption_rate", 0)
    if overall_rate > 0:
        lines.append(f"- 近30天整体采纳率：{overall_rate:.0%}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Weekly optimization entry point
# ---------------------------------------------------------------------------

async def run_weekly_optimization(db: AsyncSession) -> dict[str, Any]:
    """Run the weekly feedback optimization cycle.

    1. Compute adoption rates for the past 7 days and 30 days
    2. Analyze failure patterns
    3. Compute prompt adjustments
    4. Log everything to optimization_logs table

    Returns the optimization result.
    """
    logger.info("Starting weekly feedback optimization...")

    stats_7d = await compute_source_adoption_rates(db, days=7)
    stats_30d = await compute_source_adoption_rates(db, days=30)
    failed_patterns = await analyze_failed_recommendations(db, days=30)
    adjustments = await compute_prompt_adjustments(stats_30d, failed_patterns)

    # Build summary
    summary_parts: list[str] = []
    summary_parts.append(f"7天采纳率: {stats_7d['overall_adoption_rate']:.0%}")
    summary_parts.append(f"30天采纳率: {stats_30d['overall_adoption_rate']:.0%}")

    by_src = stats_30d.get("by_source", {})
    for src, data in by_src.items():
        rate = data.get("adoption_rate", 0)
        total = data.get("total", 0)
        summary_parts.append(f"{src}: {rate:.0%} ({total}条)")

    if failed_patterns:
        summary_parts.append(f"发现{len(failed_patterns)}个失败模式")

    summary = "; ".join(summary_parts)

    # Store optimization log
    opt_log = OptimizationLog(
        optimization_type="weekly",
        source_stats={"7d": stats_7d, "30d": stats_30d},
        failed_analysis=failed_patterns,
        prompt_adjustments=adjustments,
        summary=summary,
    )
    db.add(opt_log)
    await db.commit()

    logger.info("Weekly optimization complete: %s", summary)

    return {
        "optimization_type": "weekly",
        "stats_7d": stats_7d,
        "stats_30d": stats_30d,
        "failed_patterns": failed_patterns,
        "adjustments": adjustments,
        "summary": summary,
    }
