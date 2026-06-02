"""
LangGraph-based recommendation engine with automatic LangSmith tracing.

The recommendation workflow is modeled as a LangGraph StateGraph:
    1. load_context  — load user profile + purchase history
    2. graph_recall  — product relation graph recall
    3. vector_recall — Qdrant semantic search recall
    4. rule_recall   — purchase cycle + consumable alert recall
    5. kg_recall     — knowledge graph (treatment procedure) recall
    6. merge         — deduplicate + filter discontinued
    7. llm_rank      — LLM ranking with dynamic prompt
    8. store         — save recommendations to DB

LangSmith tracing is automatic: set LANGSMITH_API_KEY + LANGSMITH_TRACING=true
and every node execution is traced with inputs/outputs/latency.
"""

import json
import logging
import re
from dataclasses import field
from datetime import date
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class RecState(TypedDict):
    """State flowing through the recommendation graph."""
    user_id: str
    user_profile: dict
    user_purchases: list[dict]
    purchased_skus: set[str]
    graph_candidates: list[dict]
    vector_candidates: list[dict]
    rule_candidates: list[dict]
    kg_candidates: list[dict]
    all_candidates: list[dict]
    ranked: list[dict]
    db: Any  # AsyncSession
    llm_client: Any  # AsyncOpenAI
    llm_config: dict


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

async def load_context(state: RecState) -> dict:
    """Load user profile and purchase history from DB."""
    from sqlalchemy import select
    from db.models import UserProfile, UserPurchase

    db = state["db"]
    user_id = state["user_id"].strip().upper()

    # Load profile
    profile_stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    profile_result = await db.execute(profile_stmt)
    profile_row = profile_result.scalar_one_or_none()

    if profile_row is None:
        from services.user_profile import compute_profile
        user_profile = await compute_profile(user_id, db)
    else:
        user_profile = profile_row.profile_json or {}

    # Load purchases
    purchase_stmt = (
        select(UserPurchase)
        .where(UserPurchase.user_id == user_id)
        .order_by(UserPurchase.purchase_date)
    )
    purchase_result = await db.execute(purchase_stmt)
    purchase_rows = purchase_result.scalars().all()

    user_purchases = [
        {
            "sku": row.sku,
            "product_name": row.product_name,
            "quantity": row.quantity,
            "purchase_date": row.purchase_date,
        }
        for row in purchase_rows
    ]

    # Cold start: if no purchases, generate popular-product recommendations
    # directly and skip the recall pipeline
    if not user_purchases:
        from services.recommendation import _cold_start_recommendations
        ranked = await _cold_start_recommendations(user_id, db)
        return {
            "user_profile": user_profile,
            "user_purchases": [],
            "purchased_skus": set(),
            "ranked": ranked,
        }

    return {
        "user_profile": user_profile,
        "user_purchases": user_purchases,
        "purchased_skus": {p["sku"] for p in user_purchases},
    }


async def graph_recall_node(state: RecState) -> dict:
    """Graph recall: find related products via product_relations."""
    if state.get("ranked"):
        return {"graph_candidates": []}  # cold-start: skip
    try:
        from services.recommendation import graph_recall
        candidates = await graph_recall(state["user_purchases"], state["db"])
        return {"graph_candidates": candidates}
    except Exception as exc:
        logger.error("Graph recall failed: %s", exc)
        return {"graph_candidates": []}


async def vector_recall_node(state: RecState) -> dict:
    """Vector recall: Qdrant semantic search."""
    if state.get("ranked"):
        return {"vector_candidates": []}  # cold-start: skip
    try:
        from services.recommendation import vector_recall
        candidates = await vector_recall(
            state["user_profile"], state["user_purchases"], state["db"]
        )
        return {"vector_candidates": candidates}
    except Exception as exc:
        logger.error("Vector recall failed: %s", exc)
        return {"vector_candidates": []}


async def rule_recall_node(state: RecState) -> dict:
    """Rule recall: purchase cycle + consumable alerts."""
    if state.get("ranked"):
        return {"rule_candidates": []}  # cold-start: skip
    try:
        from services.recommendation import rule_recall
        candidates = await rule_recall(state["user_profile"], state["db"])
        return {"rule_candidates": candidates}
    except Exception as exc:
        logger.error("Rule recall failed: %s", exc)
        return {"rule_candidates": []}


async def kg_recall_node(state: RecState) -> dict:
    """Knowledge graph recall: treatment procedure + compatibility."""
    if state.get("ranked"):
        return {"kg_candidates": []}  # cold-start: skip
    try:
        from services.knowledge_graph import knowledge_graph_recall
        candidates = await knowledge_graph_recall(
            state["user_profile"], state["user_purchases"], state["db"]
        )
        return {"kg_candidates": candidates}
    except Exception as exc:
        logger.error("Knowledge graph recall failed: %s", exc)
        return {"kg_candidates": []}


async def merge_candidates(state: RecState) -> dict:
    """Merge all recall results, deduplicate, filter discontinued."""
    # Cold-start: ranked already populated by load_context, skip merge
    if state.get("ranked"):
        return {}

    from sqlalchemy import select
    from db.models import ProductRaw

    db = state["db"]
    purchased_skus = state["purchased_skus"]

    # Merge with score-based dedup (keep highest score per SKU)
    best_by_sku: dict[str, dict] = {}
    for c in (
        state.get("graph_candidates", [])
        + state.get("vector_candidates", [])
        + state.get("rule_candidates", [])
        + state.get("kg_candidates", [])
    ):
        sku = c.get("sku", "")
        if not sku:
            continue
        if c.get("source") != "rule" and sku in purchased_skus:
            continue
        existing = best_by_sku.get(sku)
        if not existing or c.get("score", 0) > existing.get("score", 0):
            best_by_sku[sku] = c

    all_candidates = list(best_by_sku.values())

    # Filter discontinued
    if all_candidates:
        candidate_skus = [c["sku"] for c in all_candidates]
        status_result = await db.execute(
            select(ProductRaw.sku, ProductRaw.status).where(ProductRaw.sku.in_(candidate_skus))
        )
        sku_statuses: dict[str, list[str]] = {}
        for sku, status in status_result.all():
            sku_statuses.setdefault(sku, []).append(status or "")

        DISALLOWED = {"deleted", "说明SKU"}
        unavailable = {
            sku
            for sku, statuses in sku_statuses.items()
            if all(s in DISALLOWED or "停售" in s for s in statuses)
        }
        if unavailable:
            all_candidates = [c for c in all_candidates if c["sku"] not in unavailable]

    return {"all_candidates": all_candidates}


async def llm_rank_node(state: RecState) -> dict:
    """LLM ranking: select top 3-5 with reasons."""
    # Cold-start: ranked already populated by load_context, skip ranking
    if state.get("ranked"):
        return {}

    if not state.get("all_candidates"):
        return {"ranked": []}

    from services.recommendation import _llm_rank, _fallback_rank

    config = state.get("llm_config", {})
    try:
        ranked = await _llm_rank(
            state["user_profile"],
            state["user_purchases"],
            state["all_candidates"],
            state["llm_client"],
            config.get("ranking_model", "gpt-4o"),
            config.get("temperature", 0.7),
            config.get("max_tokens", 4096),
            config.get("timeout", 30),
            db=state["db"],
        )
    except Exception as exc:
        logger.error("LLM ranking failed: %s", exc)
        ranked = []

    if not ranked:
        ranked = _fallback_rank(state["all_candidates"], limit=5)

    return {"ranked": ranked}


async def store_results(state: RecState) -> dict:
    """Store recommendations in DB."""
    from sqlalchemy import delete as sa_delete, select
    from db.models import FeedbackLog, Recommendation

    db = state["db"]
    user_id = state["user_id"]
    ranked = state.get("ranked", [])

    # Always clear old recommendations first (even if new results are empty)
    # Delete feedback logs first to avoid FK constraint violation
    old_rec_ids = await db.execute(
        select(Recommendation.id).where(Recommendation.user_id == user_id)
    )
    old_ids = [row[0] for row in old_rec_ids.all()]
    if old_ids:
        await db.execute(sa_delete(FeedbackLog).where(FeedbackLog.recommendation_id.in_(old_ids)))
    await db.execute(sa_delete(Recommendation).where(Recommendation.user_id == user_id))

    if not ranked:
        await db.commit()
        return {}

    for item in ranked[:5]:
        rec = Recommendation(
            user_id=user_id,
            recommended_sku=item.get("sku", ""),
            rank=item.get("rank", 0),
            reason=item.get("reason", ""),
            confidence=item.get("confidence", 0.0),
            source=item.get("source", "llm"),
            status="pending",
        )
        db.add(rec)

    await db.commit()
    logger.info("Stored %d recommendations for user %s", len(ranked), user_id)
    return {}


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_recommendation_graph() -> StateGraph:
    """Build the LangGraph recommendation workflow.

    Returns a compiled StateGraph that can be invoked with:
        result = await graph.ainvoke(initial_state)
    """
    graph = StateGraph(RecState)

    # Add nodes
    graph.add_node("load_context", load_context)
    graph.add_node("graph_recall", graph_recall_node)
    graph.add_node("vector_recall", vector_recall_node)
    graph.add_node("rule_recall", rule_recall_node)
    graph.add_node("kg_recall", kg_recall_node)
    graph.add_node("merge", merge_candidates)
    graph.add_node("llm_rank", llm_rank_node)
    graph.add_node("store", store_results)

    # Define edges — sequential flow with parallel recall fan-out
    graph.set_entry_point("load_context")

    # After loading context, run all 4 recall strategies in parallel.
    # Each recall node checks if ranked is already populated (cold-start)
    # and skips execution if so.
    graph.add_edge("load_context", "graph_recall")
    graph.add_edge("load_context", "vector_recall")
    graph.add_edge("load_context", "rule_recall")
    graph.add_edge("load_context", "kg_recall")

    # All recalls feed into merge
    graph.add_edge("graph_recall", "merge")
    graph.add_edge("vector_recall", "merge")
    graph.add_edge("rule_recall", "merge")
    graph.add_edge("kg_recall", "merge")

    # Merge → LLM rank → store → end
    graph.add_edge("merge", "llm_rank")
    graph.add_edge("llm_rank", "store")
    graph.add_edge("store", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Singleton graph instance
_recommendation_graph = None


def get_recommendation_graph():
    """Get or create the singleton recommendation graph."""
    global _recommendation_graph
    if _recommendation_graph is None:
        _recommendation_graph = build_recommendation_graph()
    return _recommendation_graph


async def run_recommendation_graph(
    user_id: str,
    db: Any,
    llm_client: Any,
    llm_config: dict,
) -> list[dict]:
    """Run the recommendation graph for a single user.

    This is the main entry point that replaces the old generate_recommendations().
    All node executions are automatically traced by LangSmith.
    """
    graph = get_recommendation_graph()

    initial_state: RecState = {
        "user_id": user_id,
        "user_profile": {},
        "user_purchases": [],
        "purchased_skus": set(),
        "graph_candidates": [],
        "vector_candidates": [],
        "rule_candidates": [],
        "kg_candidates": [],
        "all_candidates": [],
        "ranked": [],
        "db": db,
        "llm_client": llm_client,
        "llm_config": llm_config,
    }

    result = await graph.ainvoke(initial_state)

    # Read back the stored recommendations from DB to get real IDs
    from sqlalchemy import select
    from db.models import Recommendation

    ranked = result.get("ranked", [])
    if not ranked:
        return []

    refresh_stmt = (
        select(Recommendation)
        .where(Recommendation.user_id == user_id)
        .order_by(Recommendation.rank)
    )
    refresh_result = await db.execute(refresh_stmt)
    rec_rows = refresh_result.scalars().all()

    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "recommended_sku": row.recommended_sku,
            "rank": row.rank,
            "reason": row.reason,
            "confidence": row.confidence,
            "source": row.source,
            "status": row.status,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        }
        for row in rec_rows
    ]
