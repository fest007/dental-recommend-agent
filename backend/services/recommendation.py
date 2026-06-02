"""
Recommendation engine.

Multi-path recall (graph recall from product_relations, rule recall from
purchase cycles) + LLM ranking, producing top 3-5 recommendations per user.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ProductEnriched,
    ProductRaw,
    ProductRelation,
    Recommendation,
    UserPurchase,
    UserProfile,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph recall
# ---------------------------------------------------------------------------

async def graph_recall(
    user_purchases: list[dict],
    db: AsyncSession,
    top_k: int = 15,
) -> list[dict]:
    """Find related products via the product_relations graph.

    For each product the user has purchased, look up incoming and outgoing
    edges in ``product_relations`` and collect candidate products.

    Returns a list of candidate dicts with keys:
    ``sku``, ``product_name``, ``reason``, ``score``, ``source``.
    """
    purchased_skus = {p["sku"] for p in user_purchases}

    # Query relations where the user's purchased SKUs are either source or target
    stmt = select(ProductRelation).where(
        (ProductRelation.source_sku.in_(purchased_skus))
        | (ProductRelation.target_sku.in_(purchased_skus))
    ).order_by(ProductRelation.weight.desc())  # strongest first
    result = await db.execute(stmt)
    relations = result.scalars().all()

    # Collect ALL relations per candidate SKU, then keep the best one
    candidate_relations: dict[str, list[ProductRelation]] = {}
    for rel in relations:
        if rel.source_sku in purchased_skus and rel.target_sku not in purchased_skus:
            candidate_sku = rel.target_sku
        elif rel.target_sku in purchased_skus and rel.source_sku not in purchased_skus:
            candidate_sku = rel.source_sku
        else:
            continue
        candidate_relations.setdefault(candidate_sku, []).append(rel)

    # For each candidate, keep the strongest relation (first after sort)
    # and aggregate the count of supporting relations as a confidence boost
    candidates: list[dict] = []
    for candidate_sku, rels in candidate_relations.items():
        best_rel = rels[0]  # already sorted by weight desc
        support_count = len(rels)

        # Get the enriched product name for the candidate
        enriched_stmt = select(ProductEnriched).where(
            ProductEnriched.sku == candidate_sku
        )
        enriched_result = await db.execute(enriched_stmt)
        enriched = enriched_result.scalar_one_or_none()
        candidate_name = enriched.name if enriched else candidate_sku

        # Boost score slightly if multiple relations support this candidate
        boosted_score = min(best_rel.weight + 0.05 * (support_count - 1), 1.0)

        reason = best_rel.description or f"与已购商品存在{best_rel.relation_type}关系"
        if support_count > 1:
            reason += f"（另有{support_count - 1}条关联）"

        candidates.append({
            "sku": candidate_sku,
            "product_name": candidate_name,
            "reason": reason,
            "score": boosted_score,
            "source": "graph",
        })

    # Sort by score descending, take top_k
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_k]


# ---------------------------------------------------------------------------
# Vector recall (Qdrant semantic search with text-similarity fallback)
# ---------------------------------------------------------------------------

async def vector_recall(
    user_profile: dict,
    user_purchases: list[dict],
    db: AsyncSession,
    top_k: int = 10,
) -> list[dict]:
    """Semantic recall using Qdrant vector search.

    Strategy:
    1. Build a query vector from the user's recent purchases + profile keywords.
    2. Search Qdrant for nearest product vectors.
    3. Fall back to text-similarity scoring if Qdrant is unavailable.

    Returns a list of candidate dicts with ``source="vector"``.
    """
    purchased_skus = {p["sku"] for p in user_purchases}

    # --- Try Qdrant first ---
    try:
        from services.embedding_service import search_similar_products, generate_embedding

        # Build query text from user's recent purchases and profile
        recent_names = [p.get("product_name", "") for p in user_purchases[-10:]]
        cat_prefs = [c["category"] for c in user_profile.get("category_preference", [])[:3]]
        brand_prefs = [b["brand"] for b in user_profile.get("brand_preference", [])[:3]]

        query_parts = recent_names + cat_prefs + brand_prefs
        query_text = " ".join(filter(None, query_parts))

        if query_text.strip():
            # Generate embedding for the query
            from services import llm_config_service
            cfg = await llm_config_service.get_config()
            sync_client = __import__("openai").OpenAI(
                base_url=cfg["base_url"], api_key=cfg["api_key"]
            )
            embedding_model = cfg.get("embedding_model", "text-embedding-3-small")

            vector = await asyncio.to_thread(
                generate_embedding, query_text, sync_client, embedding_model
            )

            if vector:
                results = await search_similar_products(
                    query_vector=vector,
                    top_k=top_k + len(purchased_skus),
                    exclude_skus=purchased_skus,
                )

                if results:
                    # Look up enriched names
                    result_skus = [r["sku"] for r in results]
                    enriched_stmt = select(ProductEnriched).where(
                        ProductEnriched.sku.in_(result_skus)
                    )
                    enriched_result = await db.execute(enriched_stmt)
                    name_map = {e.sku: e.name for e in enriched_result.scalars().all()}

                    return [
                        {
                            "sku": r["sku"],
                            "product_name": name_map.get(r["sku"], r["sku"]),
                            "reason": f"语义相似度 {r['score']:.2f}",
                            "score": r["score"],
                            "source": "vector",
                        }
                        for r in results[:top_k]
                    ]
    except Exception as exc:
        logger.warning("Qdrant vector recall failed, falling back to text scoring: %s", exc)

    # --- Fallback: text-similarity scoring ---
    cat_prefs_map = {c["category"]: c["count"] for c in user_profile.get("category_preference", [])}
    brand_prefs_map = {b["brand"]: b["count"] for b in user_profile.get("brand_preference", [])}
    top_cats = set(list(cat_prefs_map.keys())[:5])
    top_brands = set(list(brand_prefs_map.keys())[:5])

    result = await db.execute(select(ProductEnriched))
    all_enriched = result.scalars().all()

    scored: list[dict] = []
    for prod in all_enriched:
        if prod.sku in purchased_skus:
            continue

        score = 0.0
        reasons: list[str] = []

        if prod.category_l1 in top_cats:
            cat_count = cat_prefs_map.get(prod.category_l1, 0)
            score += 0.3 + 0.05 * min(cat_count, 5)
            reasons.append(f"偏好品类{prod.category_l1}")

        if prod.brand and prod.brand in top_brands:
            brand_count = brand_prefs_map.get(prod.brand, 0)
            score += 0.25 + 0.05 * min(brand_count, 5)
            reasons.append(f"偏好品牌{prod.brand}")

        purchased_names = " ".join(p.get("product_name", "") for p in user_purchases[-20:])
        keywords = prod.keywords or []
        kw_hits = sum(1 for kw in keywords if kw and kw in purchased_names)
        if kw_hits > 0:
            score += 0.1 * kw_hits
            reasons.append(f"关键词匹配{kw_hits}个")

        if score > 0.3:
            scored.append({
                "sku": prod.sku,
                "product_name": prod.name,
                "reason": "；".join(reasons) if reasons else "语义相关",
                "score": min(score, 0.95),
                "source": "vector",
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Rule recall
# ---------------------------------------------------------------------------

async def rule_recall(
    user_profile: dict,
    db: AsyncSession,
) -> list[dict]:
    """Identify products the user likely needs to repurchase based on
    purchase cycle analysis and consumable alerts.

    Returns a list of candidate dicts.
    """
    candidates: list[dict] = []
    today = date.today()
    user_id = user_profile.get("user_id", "")

    # --- Purchase cycle overdue items ---
    purchase_cycle: dict = user_profile.get("purchase_cycle", {})
    for category, cycle_info in purchase_cycle.items():
        avg_days = cycle_info.get("avg_days")
        last_purchased = cycle_info.get("last_purchase_date")
        if avg_days is None or not last_purchased:
            continue

        try:
            last_date = date.fromisoformat(last_purchased)
        except (ValueError, TypeError):
            continue

        days_since = (today - last_date).days
        if days_since >= avg_days:
            # Find representative products from this category
            # that THE USER last purchased (not other users!)
            stmt = (
                select(UserPurchase)
                .where(
                    UserPurchase.user_id == user_id,
                    UserPurchase.purchase_date == last_date,
                )
                .limit(3)
            )
            result = await db.execute(stmt)
            rows = result.scalars().all()

            for row in rows:
                # Check enriched category matches
                enriched_stmt = select(ProductEnriched).where(
                    ProductEnriched.sku == row.sku
                )
                enriched_result = await db.execute(enriched_stmt)
                enriched = enriched_result.scalar_one_or_none()
                if enriched and enriched.category_l1 == category:
                    urgency = "high" if days_since >= avg_days * 1.2 else "medium"
                    candidates.append({
                        "sku": row.sku,
                        "product_name": row.product_name,
                        "reason": f"距上次采购{category}类商品已{days_since}天（平均周期{avg_days}天），建议补货",
                        "score": 0.9 if urgency == "high" else 0.7,
                        "source": "rule",
                    })

    # --- Consumable alerts ---
    alerts: list[dict] = user_profile.get("consumable_alerts", [])
    for alert in alerts:
        if alert.get("status") == "overdue":
            candidates.append({
                "sku": alert.get("sku", ""),
                "product_name": alert.get("product_name", ""),
                "reason": f"消耗品已超过建议更换周期，逾期{alert.get('days_overdue', 0)}天",
                "score": 0.95,
                "source": "rule",
            })
        elif alert.get("status") == "upcoming":
            candidates.append({
                "sku": alert.get("sku", ""),
                "product_name": alert.get("product_name", ""),
                "reason": f"消耗品即将到期，建议提前备货",
                "score": 0.6,
                "source": "rule",
            })

    # Deduplicate by SKU
    seen: set[str] = set()
    unique: list[dict] = []
    for c in candidates:
        sku = c["sku"]
        if sku and sku not in seen:
            seen.add(sku)
            unique.append(c)

    return unique


# ---------------------------------------------------------------------------
# LLM ranking
# ---------------------------------------------------------------------------

_RANKING_PROMPT_TEMPLATE = """## 任务
你是一个牙科设备推荐专家，为客服营销人员提供精准的采购推荐建议。
你需要根据用户画像和购买历史，从候选商品中选出最值得推荐的3-5个商品。
每个推荐必须附带具体的推荐理由，便于客服向客户解释。

## 用户画像
- 客户ID：{user_id}
- 客户类型：{customer_type}
- 价值分层：{value_tier}
- 主要采购品类：{top_categories}
- 偏好品牌：{top_brands}
- 最近采购日期：{last_purchase_date}

## 用户近期购买记录（最近10条）
{recent_purchases}

## 候选推荐商品（来自多路召回）
{candidates}

## 输出要求
请从候选商品中选出最值得推荐的3-5个商品，按推荐优先级排序。
只输出JSON数组，不要输出其他内容。格式如下：

[
  {{
    "rank": 1,
    "sku": "SKU编码",
    "product_name": "商品名称",
    "reason": "推荐理由（一句话，面向客服话术）",
    "confidence": 0.95
  }}
]
"""


async def _llm_rank(
    user_profile: dict,
    user_purchases: list[dict],
    candidates: list[dict],
    llm_client: Any,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    db: AsyncSession | None = None,
) -> list[dict]:
    """Use LLM to rank candidates and produce top 3-5 recommendations."""
    user_id = user_profile.get("user_id", "")
    basic_info = user_profile.get("basic_info", {})
    value_tier = user_profile.get("value_tier", "")

    # Top categories
    cat_prefs = user_profile.get("category_preference", [])
    top_categories = ", ".join(
        f"{c['category']}({c['count']}次)" for c in cat_prefs[:5]
    ) or "无"

    # Top brands
    brand_prefs = user_profile.get("brand_preference", [])
    top_brands = ", ".join(
        f"{b['brand']}({b['count']}次)" for b in brand_prefs[:5]
    ) or "无"

    last_purchase_date = basic_info.get("last_purchase_date", "无")

    # Recent purchases (last 10)
    recent = user_purchases[-10:] if len(user_purchases) > 10 else user_purchases
    recent_lines: list[str] = []
    for p in recent:
        pd = p.get("purchase_date", "")
        if isinstance(pd, date):
            pd = pd.isoformat()
        recent_lines.append(
            f"- {pd} | {p.get('sku', '')} | {p.get('product_name', '')} | x{p.get('quantity', 1)}"
        )
    recent_purchases_text = "\n".join(recent_lines) or "无购买记录"

    # Candidates
    candidate_lines: list[str] = []
    for i, c in enumerate(candidates[:20], 1):
        candidate_lines.append(
            f"{i}. [{c.get('source', '')}] {c.get('sku', '')} - {c.get('product_name', '')} "
            f"(score={c.get('score', 0):.2f}, reason={c.get('reason', '')})"
        )
    candidates_text = "\n".join(candidate_lines) or "无候选商品"

    prompt = _RANKING_PROMPT_TEMPLATE.format(
        user_id=user_id,
        customer_type=basic_info.get("customer_type", "未知"),
        value_tier=value_tier,
        top_categories=top_categories,
        top_brands=top_brands,
        last_purchase_date=last_purchase_date,
        recent_purchases=recent_purchases_text,
        candidates=candidates_text,
    )

    # Inject feedback-driven optimization hints
    if db is not None:
        try:
            from services.feedback_optimizer import build_dynamic_ranking_context
            dynamic_context = await build_dynamic_ranking_context(db)
            if dynamic_context:
                prompt = dynamic_context + "\n" + prompt
        except Exception as exc:
            logger.debug("Could not load dynamic ranking context: %s", exc)

    try:
        response = await llm_client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个牙科设备推荐专家，为客服营销人员提供精准的采购推荐建议。请严格按照要求的JSON格式输出。",
                },
                {"role": "user", "content": prompt},
            ],
        )

        raw_text = response.choices[0].message.content or ""
        # Parse JSON
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            import re
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)
            raw_text = raw_text.strip()

        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return parsed

    except Exception as exc:
        logger.error("LLM ranking failed for user %s: %s", user_id, exc, exc_info=True)

    return []


# ---------------------------------------------------------------------------
# Fallback ranking (no LLM)
# ---------------------------------------------------------------------------

def _fallback_rank(candidates: list[dict], limit: int = 5) -> list[dict]:
    """Simple score-based ranking when LLM is unavailable."""
    sorted_cands = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)
    results: list[dict] = []
    for i, c in enumerate(sorted_cands[:limit], 1):
        results.append({
            "rank": i,
            "sku": c.get("sku", ""),
            "product_name": c.get("product_name", ""),
            "reason": c.get("reason", ""),
            "confidence": round(c.get("score", 0.5), 2),
        })
    return results


# ---------------------------------------------------------------------------
# Cold-start fallback (§15: risk mitigation)
# ---------------------------------------------------------------------------

async def _cold_start_recommendations(
    user_id: str,
    db: AsyncSession,
    limit: int = 5,
) -> list[dict]:
    """Generate recommendations for users with no purchase history.

    Falls back to frequently purchased products across all users,
    prioritizing active/enriched products.
    """
    from collections import Counter

    # Count most frequently purchased SKUs across all users
    purchase_stmt = select(UserPurchase.sku, UserPurchase.product_name)
    result = await db.execute(purchase_stmt)
    rows = result.all()

    sku_counter: Counter = Counter()
    sku_names: dict[str, str] = {}
    for sku, name in rows:
        if sku:
            sku_counter[sku] += 1
            if name:
                sku_names[sku] = name

    if not sku_counter:
        return []

    # Get top candidates
    top_skus = [sku for sku, _ in sku_counter.most_common(limit * 3)]

    # Filter out discontinued products
    raw_result = await db.execute(
        select(ProductRaw.sku, ProductRaw.status).where(ProductRaw.sku.in_(top_skus))
    )
    active_skus: set[str] = set()
    for sku, status in raw_result.all():
        if status and "停售" not in status and status not in ("deleted", "说明SKU"):
            active_skus.add(sku)

    # Build recommendations
    ranked: list[dict] = []
    rank = 0
    for sku, count in sku_counter.most_common(limit * 3):
        if sku not in active_skus:
            continue
        rank += 1
        ranked.append({
            "rank": rank,
            "sku": sku,
            "product_name": sku_names.get(sku, sku),
            "reason": f"热门商品（全站采购{count}次），适合新客户尝试",
            "confidence": round(min(0.5 + 0.05 * count, 0.9), 2),
        })
        if rank >= limit:
            break

    if not ranked:
        return []

    # Tag with source for downstream consumers
    for item in ranked:
        item["source"] = "cold_start"

    logger.info("Cold-start candidates computed for user %s: %d items", user_id, len(ranked))
    return ranked


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_recommendations(
    user_id: str,
    db: AsyncSession,
    llm_service: Any,
) -> list[dict]:
    """Generate recommendations for a single user.

    Workflow:
    1. Load user profile and purchase history.
    2. Run multi-path recall (graph + rule).
    3. Merge and deduplicate candidates.
    4. Call LLM to rank top 3-5 (fallback to score-based ranking).
    5. Store results in the recommendations table.

    Parameters
    ----------
    user_id : str
        The user/customer ID.
    db : AsyncSession
        Database session.
    llm_service : Any
        An AsyncOpenAI client instance from llm_config_service.

    Returns
    -------
    list[dict]
        The stored recommendation records.
    """
    # ------------------------------------------------------------------
    # 1. Load user profile
    # ------------------------------------------------------------------
    profile_stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    profile_result = await db.execute(profile_stmt)
    profile_row = profile_result.scalar_one_or_none()

    if profile_row is None:
        logger.warning("No profile found for user %s; computing one first.", user_id)
        from services.user_profile import compute_profile
        user_profile = await compute_profile(user_id, db)
    else:
        user_profile = profile_row.profile_json or {}

    # ------------------------------------------------------------------
    # 2. Load purchase history
    # ------------------------------------------------------------------
    purchase_stmt = (
        select(UserPurchase)
        .where(UserPurchase.user_id == user_id)
        .order_by(UserPurchase.purchase_date)
    )
    purchase_result = await db.execute(purchase_stmt)
    purchase_rows = purchase_result.scalars().all()

    user_purchases: list[dict] = []
    for row in purchase_rows:
        user_purchases.append({
            "sku": row.sku,
            "product_name": row.product_name,
            "quantity": row.quantity,
            "purchase_date": row.purchase_date,
        })

    if not user_purchases:
        logger.info("No purchases for user %s; using cold-start fallback.", user_id)
        return await _cold_start_recommendations(user_id, db)

    purchased_skus = {p["sku"] for p in user_purchases}

    # ------------------------------------------------------------------
    # 3. Multi-path recall (graph + vector + rule + knowledge_graph)
    # ------------------------------------------------------------------
    graph_candidates: list[dict] = []
    vector_candidates: list[dict] = []
    rule_candidates: list[dict] = []
    kg_candidates: list[dict] = []

    try:
        graph_candidates = await graph_recall(user_purchases, db)
    except Exception as exc:
        logger.error("Graph recall failed for user %s: %s", user_id, exc)

    try:
        vector_candidates = await vector_recall(user_profile, user_purchases, db)
    except Exception as exc:
        logger.error("Vector recall failed for user %s: %s", user_id, exc)

    try:
        rule_candidates = await rule_recall(user_profile, db)
    except Exception as exc:
        logger.error("Rule recall failed for user %s: %s", user_id, exc)

    try:
        from services.knowledge_graph import knowledge_graph_recall
        kg_candidates = await knowledge_graph_recall(user_profile, user_purchases, db)
    except Exception as exc:
        logger.error("Knowledge graph recall failed for user %s: %s", user_id, exc)

    # Merge and deduplicate
    # Rule-recall candidates (repurchase items) are kept even if SKU is in
    # purchased_skus — that's the whole point of cycle-based补货.
    seen_skus: set[str] = set()
    all_candidates: list[dict] = []
    for c in graph_candidates + vector_candidates + rule_candidates + kg_candidates:
        sku = c.get("sku", "")
        if not sku or sku in seen_skus:
            continue
        # Only filter out graph candidates for already-purchased SKUs
        if c.get("source") != "rule" and sku in purchased_skus:
            continue
        seen_skus.add(sku)
        all_candidates.append(c)

    if not all_candidates:
        logger.info("No candidates found for user %s.", user_id)
        return []

    # ------------------------------------------------------------------
    # 3b. Filter out discontinued / inactive products
    # Only exclude a SKU if ALL its records are discontinued.
    # A SKU with at least one active record (A1在售, A3N在售, etc.) is kept.
    # ------------------------------------------------------------------
    candidate_skus = [c["sku"] for c in all_candidates]
    status_result = await db.execute(
        select(ProductRaw.sku, ProductRaw.status).where(ProductRaw.sku.in_(candidate_skus))
    )

    # Group statuses by SKU
    sku_statuses: dict[str, list[str]] = {}
    for sku, status in status_result.all():
        sku_statuses.setdefault(sku, []).append(status or "")

    DISALLOWED_STATUSES = {"deleted", "说明SKU"}
    unavailable_skus: set[str] = set()
    for sku, statuses in sku_statuses.items():
        # A SKU is unavailable only if ALL its records are disallowed or discontinued
        all_disallowed = all(
            s in DISALLOWED_STATUSES or "停售" in s
            for s in statuses
        )
        if all_disallowed:
            unavailable_skus.add(sku)

    if unavailable_skus:
        all_candidates = [c for c in all_candidates if c["sku"] not in unavailable_skus]
        logger.info(
            "Filtered %d fully-discontinued SKUs for user %s: %s",
            len(unavailable_skus), user_id, unavailable_skus,
        )

    if not all_candidates:
        logger.info("All candidates were discontinued for user %s.", user_id)
        return []

    # ------------------------------------------------------------------
    # 4. LLM ranking (with fallback)
    # ------------------------------------------------------------------
    from services import llm_config_service

    config = await llm_config_service.get_config()
    model = config.get("ranking_model", "gpt-4o")
    temperature = config.get("temperature", 0.7)
    max_tokens = config.get("max_tokens", 4096)
    timeout = config.get("timeout", 30)

    ranked: list[dict] = []
    try:
        ranked = await _llm_rank(
            user_profile,
            user_purchases,
            all_candidates,
            llm_service,
            model,
            temperature,
            max_tokens,
            timeout,
            db=db,
        )
    except Exception as exc:
        logger.error("LLM ranking failed: %s; using fallback.", exc)

    if not ranked:
        logger.info("LLM returned no results; using fallback ranking.")
        ranked = _fallback_rank(all_candidates, limit=5)

    # ------------------------------------------------------------------
    # 5. Store recommendations
    # ------------------------------------------------------------------
    # Clear old recommendations for this user
    # Delete feedback logs first to avoid FK constraint violation
    from db.models import FeedbackLog
    old_rec_ids = await db.execute(
        select(Recommendation.id).where(Recommendation.user_id == user_id)
    )
    old_ids = [row[0] for row in old_rec_ids.all()]
    if old_ids:
        await db.execute(delete(FeedbackLog).where(FeedbackLog.recommendation_id.in_(old_ids)))
    del_stmt = delete(Recommendation).where(Recommendation.user_id == user_id)
    await db.execute(del_stmt)

    stored: list[dict] = []
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
        stored.append({
            "user_id": user_id,
            "recommended_sku": item.get("sku", ""),
            "rank": item.get("rank", 0),
            "reason": item.get("reason", ""),
            "confidence": item.get("confidence", 0.0),
            "source": item.get("source", "llm"),
            "status": "pending",
        })

    await db.commit()

    # Refresh to get IDs
    refresh_stmt = (
        select(Recommendation)
        .where(Recommendation.user_id == user_id)
        .order_by(Recommendation.rank)
    )
    refresh_result = await db.execute(refresh_stmt)
    rec_rows = refresh_result.scalars().all()

    result_list: list[dict] = []
    for row in rec_rows:
        result_list.append({
            "id": row.id,
            "user_id": row.user_id,
            "recommended_sku": row.recommended_sku,
            "rank": row.rank,
            "reason": row.reason,
            "confidence": row.confidence,
            "source": row.source,
            "status": row.status,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        })

    logger.info("Generated %d recommendations for user %s.", len(result_list), user_id)
    return result_list
