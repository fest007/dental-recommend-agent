"""
Industry knowledge graph for dental treatment workflows.

Three-layer knowledge system (tech_design.md section 12):
- Layer 1: Product Relations Graph (already in relation_builder.py)
- Layer 2: Industry Knowledge Graph — treatment procedures → required products
- Layer 3: Clinical Scenario Graph — disease/symptom → treatment plan → products

This module implements Layer 2 & 3, providing:
1. Treatment procedure definitions (root canal, implant, etc.)
2. Product compatibility rules (brand/system matching)
3. Three recall strategies: procedure recall, compatibility recall, scenario recall
4. LLM-based automatic relation inference (§12.4)
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ProductEnriched, ProductRelation, UserPurchase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Treatment procedure definitions (tech_design.md 12.2)
# ---------------------------------------------------------------------------

TREATMENT_PROCEDURES: list[dict] = [
    {
        "name": "根管治疗",
        "scenario_keywords": ["根管", "牙髓", "根尖", "牙体"],
        "steps": [
            {"step": 1, "action": "开髓", "product_keywords": ["裂钻", "球钻", "开髓"]},
            {"step": 2, "action": "根管预备", "product_keywords": ["根管锉", "根管测量仪", "机用锉", "Protaper", "WaveOne"]},
            {"step": 3, "action": "根管冲洗", "product_keywords": ["次氯酸钠", "EDTA", "冲洗", "根管冲洗"]},
            {"step": 4, "action": "根管充填", "product_keywords": ["牙胶尖", "根管封闭剂", "充填"]},
            {"step": 5, "action": "冠修复", "product_keywords": ["树脂水门汀", "临时冠", "全冠", "嵌体"]},
        ],
    },
    {
        "name": "种植修复",
        "scenario_keywords": ["种植", "植入", "种植体", "种植修复"],
        "steps": [
            {"step": 1, "action": "术前检查", "product_keywords": ["CBCT", "口内扫描仪", "CT"]},
            {"step": 2, "action": "种植手术", "product_keywords": ["种植体", "骨粉", "骨膜", "Bio-Oss"]},
            {"step": 3, "action": "愈合期", "product_keywords": ["愈合基台", "愈合帽"]},
            {"step": 4, "action": "取模", "product_keywords": ["印模材", "印模托盘", "转移杆"]},
            {"step": 5, "action": "戴牙", "product_keywords": ["基台", "螺丝", "树脂水门汀", "CAD/CAM"]},
        ],
    },
    {
        "name": "正畸治疗",
        "scenario_keywords": ["正畸", "矫正", "托槽", "隐形矫治"],
        "steps": [
            {"step": 1, "action": "诊断取模", "product_keywords": ["硅橡胶", "印模材", "头颅定位"]},
            {"step": 2, "action": "粘接托槽", "product_keywords": ["托槽", "粘接剂", "酸蚀"]},
            {"step": 3, "action": "弓丝调整", "product_keywords": ["弓丝", "镍钛丝", "不锈钢丝"]},
            {"step": 4, "action": "橡皮链牵引", "product_keywords": ["橡皮链", "弹力线", "橡皮圈"]},
            {"step": 5, "action": "保持器", "product_keywords": ["保持器", "压膜保持器"]},
        ],
    },
    {
        "name": "牙周治疗",
        "scenario_keywords": ["牙周", "龈下", "刮治", "牙龈"],
        "steps": [
            {"step": 1, "action": "牙周检查", "product_keywords": ["牙周探针", "牙周袋"]},
            {"step": 2, "action": "龈上洁治", "product_keywords": ["超声洁牙机", "洁治器", "抛光膏"]},
            {"step": 3, "action": "龈下刮治", "product_keywords": ["刮治器", "Gracey", "龈下"]},
            {"step": 4, "action": "牙周上药", "product_keywords": ["牙周", "派丽奥", "盐酸米诺环素"]},
            {"step": 5, "action": "牙周手术", "product_keywords": ["牙周手术刀", "骨锉", "引导组织再生"]},
        ],
    },
    {
        "name": "美学修复",
        "scenario_keywords": ["美学", "贴面", "全瓷", "美白"],
        "steps": [
            {"step": 1, "action": "美学设计", "product_keywords": ["比色板", "数码微笑设计"]},
            {"step": 2, "action": "牙体预备", "product_keywords": ["预备车针", "肩台车针"]},
            {"step": 3, "action": "取模/扫描", "product_keywords": ["印模材", "口内扫描仪"]},
            {"step": 4, "action": "临时修复", "product_keywords": ["临时冠材料", "临时粘接"]},
            {"step": 5, "action": "永久粘接", "product_keywords": ["树脂水门汀", "氢氟酸", "硅烷偶联剂"]},
        ],
    },
]


# ---------------------------------------------------------------------------
# Product compatibility rules (tech_design.md 12.2)
# ---------------------------------------------------------------------------

PRODUCT_COMPATIBILITY: list[dict] = [
    {
        "brand": "Straumann",
        "system": "BLT",
        "implant_keywords": ["Straumann", "BLT", "Straumann BLT"],
        "compatible_keywords": ["BLT 基台", "BLT 愈合", "Straumann 扭矩扳手", "Straumann 基台"],
    },
    {
        "brand": "Nobel Biocare",
        "system": "NobelActive",
        "implant_keywords": ["Nobel", "NobelActive", "Nobel Replace"],
        "compatible_keywords": ["Nobel 基台", "Nobel 愈合基台", "Nobel 扭矩扳手"],
    },
    {
        "brand": "Osstem",
        "system": "TSIII",
        "implant_keywords": ["Osstem", "奥齿泰", "TSIII"],
        "compatible_keywords": ["Osstem 基台", "奥齿泰基台", "TSIII 愈合"],
    },
    {
        "brand": "Dentsply Sirona",
        "system": "Ankylos",
        "implant_keywords": ["Ankylos", "Dentsply", "登士柏"],
        "compatible_keywords": ["Ankylos 基台", "Astra 基台"],
    },
    {
        "brand": "3M",
        "system": "ESPE",
        "implant_keywords": ["3M", "ESPE", "Lava"],
        "compatible_keywords": ["3M 树脂", "3M 粘接", "RelyX", "Filtek"],
    },
]


# ---------------------------------------------------------------------------
# Scenario inference from purchase history
# ---------------------------------------------------------------------------

def _infer_treatment_scenarios(
    user_purchases: list[dict],
    enriched_map: dict[str, ProductEnriched],
) -> list[dict]:
    """Infer which treatment scenarios the user is likely engaged in
    based on their purchase history keywords.

    Returns list of {scenario, matched_steps, confidence}.
    """
    # Build a text corpus from purchased product names + keywords
    purchased_texts: list[str] = []
    for p in user_purchases:
        name = p.get("product_name", "")
        purchased_texts.append(name)
        sku = p.get("sku", "")
        enriched = enriched_map.get(sku)
        if enriched:
            purchased_texts.extend(enriched.keywords or [])
            purchased_texts.append(enriched.usage_scenario or "")
            purchased_texts.append(enriched.category_l1 or "")
            purchased_texts.append(enriched.category_l2 or "")

    corpus = " ".join(purchased_texts).lower()

    scenarios: list[dict] = []
    for proc in TREATMENT_PROCEDURES:
        # Check scenario keywords
        scenario_hits = sum(1 for kw in proc["scenario_keywords"] if kw in corpus)

        # Check how many steps have matching products
        matched_steps: list[dict] = []
        for step in proc["steps"]:
            step_hits = sum(1 for kw in step["product_keywords"] if kw.lower() in corpus)
            if step_hits > 0:
                matched_steps.append({
                    "step": step["step"],
                    "action": step["action"],
                    "hits": step_hits,
                })

        if scenario_hits > 0 or len(matched_steps) >= 2:
            confidence = min(0.3 * scenario_hits + 0.15 * len(matched_steps), 0.95)
            scenarios.append({
                "scenario": proc["name"],
                "matched_steps": matched_steps,
                "total_steps": len(proc["steps"]),
                "confidence": round(confidence, 2),
            })

    scenarios.sort(key=lambda x: x["confidence"], reverse=True)
    return scenarios


# ---------------------------------------------------------------------------
# Recall 1: Procedure-based recall
# ---------------------------------------------------------------------------

async def _procedure_recall(
    scenarios: list[dict],
    purchased_skus: set[str],
    enriched_map: dict[str, ProductEnriched],
    all_enriched: list[ProductEnriched],
    top_k: int = 10,
) -> list[dict]:
    """Recommend products from subsequent treatment steps.

    If user bought step N products, recommend step N+1 products.
    """
    candidates: list[dict] = []

    for scenario in scenarios:
        proc_name = scenario["scenario"]
        proc = next((p for p in TREATMENT_PROCEDURES if p["name"] == proc_name), None)
        if not proc:
            continue

        matched_step_nums = {s["step"] for s in scenario["matched_steps"]}
        # Recommend products from the next unmatched steps
        next_steps = [s for s in proc["steps"] if s["step"] not in matched_step_nums]
        if not next_steps:
            continue

        for step in next_steps[:2]:  # limit to 2 next steps
            for kw in step["product_keywords"]:
                kw_lower = kw.lower()
                for prod in all_enriched:
                    if prod.sku in purchased_skus:
                        continue
                    prod_text = f"{prod.name} {' '.join(prod.keywords or [])}".lower()
                    if kw_lower in prod_text:
                        candidates.append({
                            "sku": prod.sku,
                            "product_name": prod.name,
                            "reason": f"治疗流程「{proc_name}」第{step['step']}步「{step['action']}」所需",
                            "score": 0.85 * scenario["confidence"],
                            "source": "knowledge_graph",
                        })
                        break  # one match per keyword is enough

    # Deduplicate by SKU, keep highest score
    seen: dict[str, dict] = {}
    for c in candidates:
        sku = c["sku"]
        if sku not in seen or c["score"] > seen[sku]["score"]:
            seen[sku] = c
    results = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# Recall 2: Compatibility-based recall
# ---------------------------------------------------------------------------

async def _compatibility_recall(
    user_purchases: list[dict],
    enriched_map: dict[str, ProductEnriched],
    all_enriched: list[ProductEnriched],
    purchased_skus: set[str],
    top_k: int = 8,
) -> list[dict]:
    """Recommend compatible products based on brand/system matching.

    If user bought a Straumann BLT implant, recommend BLT abutments.
    """
    candidates: list[dict] = []

    for p in user_purchases:
        sku = p.get("sku", "")
        enriched = enriched_map.get(sku)
        if not enriched:
            continue

        prod_text = f"{enriched.name} {' '.join(enriched.keywords or [])} {enriched.brand}".lower()

        for compat in PRODUCT_COMPATIBILITY:
            # Check if this purchase matches an implant system
            system_match = any(kw.lower() in prod_text for kw in compat["implant_keywords"])
            if not system_match:
                continue

            # Find compatible products
            for kw in compat["compatible_keywords"]:
                kw_lower = kw.lower()
                for candidate in all_enriched:
                    if candidate.sku in purchased_skus:
                        continue
                    c_text = f"{candidate.name} {' '.join(candidate.keywords or [])}".lower()
                    if kw_lower in c_text:
                        candidates.append({
                            "sku": candidate.sku,
                            "product_name": candidate.name,
                            "reason": f"与已购{compat['brand']} {compat['system']}系统兼容",
                            "score": 0.9,
                            "source": "knowledge_graph",
                        })
                        break

    # Deduplicate
    seen: dict[str, dict] = {}
    for c in candidates:
        sku = c["sku"]
        if sku not in seen or c["score"] > seen[sku]["score"]:
            seen[sku] = c
    results = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# Recall 3: Scenario-based recall
# ---------------------------------------------------------------------------

async def _scenario_recall(
    scenarios: list[dict],
    user_profile: dict,
    all_enriched: list[ProductEnriched],
    purchased_skus: set[str],
    top_k: int = 8,
) -> list[dict]:
    """Recommend products based on inferred clinical scenarios.

    If user frequently buys orthodontic products, recommend related consumables.
    """
    if not scenarios:
        return []

    # Use top scenario to find related products
    top_scenario = scenarios[0]
    proc_name = top_scenario["scenario"]
    proc = next((p for p in TREATMENT_PROCEDURES if p["name"] == proc_name), None)
    if not proc:
        return []

    # Collect all keywords from all steps
    all_keywords: set[str] = set()
    for step in proc["steps"]:
        for kw in step["product_keywords"]:
            all_keywords.add(kw.lower())

    # Also use category preferences from profile
    cat_prefs = {c["category"] for c in user_profile.get("category_preference", [])[:3]}

    candidates: list[dict] = []
    for prod in all_enriched:
        if prod.sku in purchased_skus:
            continue

        prod_text = f"{prod.name} {' '.join(prod.keywords or [])} {prod.category_l1 or ''}".lower()

        # Match against procedure keywords
        kw_hits = sum(1 for kw in all_keywords if kw in prod_text)
        if kw_hits == 0:
            continue

        # Boost if category matches user preference
        cat_boost = 0.1 if prod.category_l1 in cat_prefs else 0.0

        score = min(0.5 + 0.1 * kw_hits + cat_boost, 0.95)
        candidates.append({
            "sku": prod.sku,
            "product_name": prod.name,
            "reason": f"与{proc_name}场景相关（匹配{kw_hits}个关键词）",
            "score": round(score, 2),
            "source": "knowledge_graph",
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_k]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def knowledge_graph_recall(
    user_profile: dict,
    user_purchases: list[dict],
    db: AsyncSession,
    top_k: int = 15,
) -> list[dict]:
    """Main entry point for knowledge graph recall.

    Combines three recall strategies:
    1. Procedure recall — next-step products based on treatment workflow
    2. Compatibility recall — brand/system compatible products
    3. Scenario recall — products related to inferred clinical scenario

    Returns a list of candidate dicts with ``source="knowledge_graph"``.
    """
    purchased_skus = {p["sku"] for p in user_purchases}

    # Load enriched data
    all_enriched_result = await db.execute(select(ProductEnriched))
    all_enriched = all_enriched_result.scalars().all()

    enriched_map: dict[str, ProductEnriched] = {}
    for p in user_purchases:
        enriched_result = await db.execute(
            select(ProductEnriched).where(ProductEnriched.sku == p["sku"])
        )
        enriched = enriched_result.scalar_one_or_none()
        if enriched:
            enriched_map[p["sku"]] = enriched

    # Infer treatment scenarios
    scenarios = _infer_treatment_scenarios(user_purchases, enriched_map)

    if not scenarios:
        logger.info("No treatment scenarios inferred; skipping knowledge graph recall.")
        return []

    logger.info(
        "Inferred %d treatment scenarios: %s",
        len(scenarios),
        [s["scenario"] for s in scenarios],
    )

    # Run all three recall strategies
    procedure_results = await _procedure_recall(
        scenarios, purchased_skus, enriched_map, all_enriched
    )
    compatibility_results = await _compatibility_recall(
        user_purchases, enriched_map, all_enriched, purchased_skus
    )
    scenario_results = await _scenario_recall(
        scenarios, user_profile, all_enriched, purchased_skus
    )

    # Merge and deduplicate
    all_candidates: list[dict] = []
    seen_skus: set[str] = set()
    for c in procedure_results + compatibility_results + scenario_results:
        sku = c["sku"]
        if sku not in seen_skus:
            seen_skus.add(sku)
            all_candidates.append(c)

    all_candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Knowledge graph recall: %d candidates", len(all_candidates))
    return all_candidates[:top_k]


# ---------------------------------------------------------------------------
# LLM-based knowledge graph construction (§12.4)
# ---------------------------------------------------------------------------

_INFER_RELATIONS_PROMPT = """你是一个牙科设备行业专家。以下是一组"{category}"品类的商品，请判断它们之间的治疗流程关系。

商品列表：
{product_list}

请分析这些商品在牙科治疗流程中的关系，输出JSON数组。每条关系包含：
- source_sku: 源商品SKU
- target_sku: 目标商品SKU
- relation_type: 关系类型（consumable_of/accessory_of/complementary/same_series/consumes_with/upgrade_to）
- weight: 关系强度 0-1
- description: 关系描述（一句话）

重点关注：
1. 设备与其配套消耗品/配件的关系
2. 治疗流程中先后使用的产品关系（如开髓→根管预备→根管冲洗→根管充填）
3. 同系列不同型号的产品关系
4. 需要配套使用的产品关系

只输出JSON数组，不要输出其他内容。如果没有明显关系，输出空数组 []。"""


async def build_relations_with_llm(
    db: AsyncSession,
    batch_size: int = 30,
    max_products_per_group: int = 20,
) -> dict[str, int]:
    """Use LLM to automatically infer treatment workflow relations between products.

    Groups enriched products by category_l1, sends each group to LLM for
    relation inference, and writes discovered relations to product_relations table.

    Parameters
    ----------
    db : AsyncSession
    batch_size : int
        Number of products per LLM call batch.
    max_products_per_group : int
        Max products to include per category group to avoid token limits.

    Returns
    -------
    dict with counts: {"groups_processed": N, "relations_discovered": N, "relations_saved": N}
    """
    from services import llm_config_service
    from openai import OpenAI

    config = await llm_config_service.get_config()
    if not config.get("api_key"):
        logger.warning("No LLM API key configured; skipping LLM relation inference.")
        return {"groups_processed": 0, "relations_discovered": 0, "relations_saved": 0, "error": "no_api_key"}

    sync_client = OpenAI(base_url=config["base_url"], api_key=config["api_key"])
    model = config.get("enrichment_model", "gpt-4o-mini")

    # Load all enriched products
    result = await db.execute(select(ProductEnriched))
    enriched = result.scalars().all()

    if not enriched:
        return {"groups_processed": 0, "relations_discovered": 0, "relations_saved": 0}

    # Group by category_l1
    category_groups: dict[str, list[ProductEnriched]] = defaultdict(list)
    for prod in enriched:
        cat = prod.category_l1 or "其他"
        category_groups[cat].append(prod)

    # Load existing relations to avoid duplicates
    existing_result = await db.execute(
        select(ProductRelation.source_sku, ProductRelation.target_sku, ProductRelation.relation_type)
    )
    existing_keys: set[tuple[str, str, str]] = {
        (r[0], r[1], r[2]) for r in existing_result.all()
    }

    groups_processed = 0
    relations_discovered = 0
    relations_saved = 0

    for category, products in category_groups.items():
        if len(products) < 2:
            continue

        # Sample products to avoid token limits
        sample = products[:max_products_per_group]

        # Build product list text
        product_lines: list[str] = []
        for p in sample:
            keywords = ", ".join(p.keywords[:5]) if p.keywords else ""
            product_lines.append(
                f"- SKU: {p.sku} | 名称: {p.name} | 品牌: {p.brand} | "
                f"类型: {p.product_type} | 场景: {p.usage_scenario} | 关键词: {keywords}"
            )
        product_list = "\n".join(product_lines)

        prompt = _INFER_RELATIONS_PROMPT.format(
            category=category,
            product_list=product_list,
        )

        try:
            response = await asyncio.to_thread(
                sync_client.chat.completions.create,
                model=model,
                temperature=0.3,
                max_tokens=4096,
                timeout=60,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个牙科设备行业专家，擅长分析产品之间的治疗流程关系。请严格按照要求的JSON格式输出。",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            raw_text = response.choices[0].message.content or ""
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
                raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            if not isinstance(parsed, list):
                parsed = []

            groups_processed += 1

        except Exception as exc:
            logger.error("LLM relation inference failed for category '%s': %s", category, exc)
            continue

        # Validate and save relations
        valid_skus = {p.sku for p in sample}
        for rel in parsed:
            source_sku = rel.get("source_sku", "")
            target_sku = rel.get("target_sku", "")
            relation_type = rel.get("relation_type", "")
            weight = rel.get("weight", 0.5)
            description = rel.get("description", "")

            # Validate
            if not source_sku or not target_sku or source_sku == target_sku:
                continue
            if source_sku not in valid_skus or target_sku not in valid_skus:
                continue
            valid_types = {"consumable_of", "accessory_of", "complementary", "same_series", "consumes_with", "upgrade_to", "same_category"}
            if relation_type not in valid_types:
                continue

            relations_discovered += 1

            # Check for duplicate
            key = (source_sku, target_sku, relation_type)
            if key in existing_keys:
                continue

            existing_keys.add(key)
            rel_record = ProductRelation(
                source_sku=source_sku,
                target_sku=target_sku,
                relation_type=relation_type,
                weight=min(max(float(weight), 0.0), 1.0),
                description=description or f"LLM推断: {relation_type}",
                source="llm_infer",
            )
            db.add(rel_record)
            relations_saved += 1

        logger.info(
            "LLM relation inference for '%s': %d products, %d relations found",
            category, len(sample), len(parsed),
        )

    await db.commit()

    result = {
        "groups_processed": groups_processed,
        "relations_discovered": relations_discovered,
        "relations_saved": relations_saved,
    }
    logger.info("LLM knowledge graph construction complete: %s", result)
    return result
