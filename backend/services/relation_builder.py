"""
Product relation builder.

Generates product_relations from enriched product data + purchase records:
1. consumable_of / accessory_of — from LLM-enriched consumables/related_accessories fields
2. same_category — products sharing category_l1 + brand
3. complementary — products in the same category_l1 but different category_l2
4. same_series — SKU prefix rule (e.g. XH0027-1 ↔ XH0027-2)
5. co_purchased — frequently co-occurring in purchase records
"""

import logging
import re
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ProductEnriched, ProductRelation, UserPurchase

logger = logging.getLogger(__name__)


async def build_relations(
    db: AsyncSession,
    clear_existing: bool = False,
) -> dict[str, int]:
    """Build product_relations from enriched product data.

    Parameters
    ----------
    db : AsyncSession
    clear_existing : bool
        If True, delete all existing relations before rebuilding.

    Returns
    -------
    dict with counts: {"consumable": N, "accessory": N, "same_category": N, "complementary": N, "same_series": N, "co_purchased": N, "total": N}
    """
    if clear_existing:
        await db.execute(sa_delete(ProductRelation))

    # Load all enriched products
    result = await db.execute(select(ProductEnriched))
    enriched = result.scalars().all()

    if not enriched:
        logger.warning("No enriched products found; cannot build relations.")
        return {"consumable": 0, "accessory": 0, "same_category": 0, "complementary": 0, "total": 0}

    # Index by SKU for fast lookup
    by_sku: dict[str, ProductEnriched] = {p.sku: p for p in enriched}

    # Track existing relations to avoid duplicates
    existing_result = await db.execute(
        select(ProductRelation.source_sku, ProductRelation.target_sku, ProductRelation.relation_type)
    )
    existing_keys: set[tuple[str, str, str]] = {
        (r[0], r[1], r[2]) for r in existing_result.all()
    }

    counts = {"consumable": 0, "accessory": 0, "same_category": 0, "complementary": 0, "same_series": 0, "co_purchased": 0, "upgrade_to": 0, "consumes_with": 0}
    now = datetime.now(timezone.utc)

    def _add_relation(source_sku: str, target_sku: str, rel_type: str, weight: float, desc: str) -> bool:
        """Add a relation if it doesn't already exist. Returns True if added."""
        key = (source_sku, target_sku, rel_type)
        if key in existing_keys:
            return False
        existing_keys.add(key)
        rel = ProductRelation(
            source_sku=source_sku,
            target_sku=target_sku,
            relation_type=rel_type,
            weight=weight,
            description=desc,
            source="llm_enriched",
        )
        db.add(rel)
        return True

    # --- 1. consumable_of / accessory_of from enriched fields ---
    for prod in enriched:
        # consumables → consumable_of relation
        consumables = prod.consumables or []
        for item in consumables:
            if isinstance(item, dict):
                target_name = item.get("name", "")
            elif isinstance(item, str):
                target_name = item
            else:
                continue
            if not target_name:
                continue
            # Try to find the target SKU by name match
            target_sku = _find_sku_by_name(target_name, by_sku)
            if target_sku and target_sku != prod.sku:
                if _add_relation(prod.sku, target_sku, "consumable_of", 0.9,
                                 f"{prod.name}的配套消耗品"):
                    counts["consumable"] += 1

        # related_accessories → accessory_of relation
        accessories = prod.related_accessories or []
        for item in accessories:
            if isinstance(item, dict):
                target_name = item.get("name", "")
            elif isinstance(item, str):
                target_name = item
            else:
                continue
            if not target_name:
                continue
            target_sku = _find_sku_by_name(target_name, by_sku)
            if target_sku and target_sku != prod.sku:
                if _add_relation(prod.sku, target_sku, "accessory_of", 0.85,
                                 f"{prod.name}的适配配件"):
                    counts["accessory"] += 1

    # --- 2. same_category — same category_l1 + same brand ---
    # Group by (category_l1, brand)
    cat_brand_groups: dict[tuple[str, str], list[ProductEnriched]] = {}
    for prod in enriched:
        if prod.category_l1 and prod.brand:
            key = (prod.category_l1, prod.brand)
            cat_brand_groups.setdefault(key, []).append(prod)

    for (cat, brand), group in cat_brand_groups.items():
        if len(group) < 2:
            continue
        # Create relations between all pairs (limit to avoid explosion)
        for i, p1 in enumerate(group[:20]):  # cap at 20 per group
            for p2 in group[i + 1 : 21]:
                if p1.sku == p2.sku:
                    continue
                desc = f"同品牌同品类: {brand} {cat}"
                _add_relation(p1.sku, p2.sku, "same_category", 0.7, desc)
                _add_relation(p2.sku, p1.sku, "same_category", 0.7, desc)
                counts["same_category"] += 2

    # --- 3. complementary — same category_l1, different category_l2 ---
    cat_groups: dict[str, list[ProductEnriched]] = {}
    for prod in enriched:
        if prod.category_l1:
            cat_groups.setdefault(prod.category_l1, []).append(prod)

    for cat, group in cat_groups.items():
        # Group by category_l2 within this category_l1
        l2_groups: dict[str, list[ProductEnriched]] = {}
        for p in group:
            l2_groups.setdefault(p.category_l2 or "其他", []).append(p)

        l2_keys = list(l2_groups.keys())
        if len(l2_keys) < 2:
            continue

        # Create complementary relations between different l2 groups (sample)
        for i, l2a in enumerate(l2_keys[:5]):
            for l2b in l2_keys[i + 1 : 6]:
                # Pick up to 2 representative products from each group
                reps_a = l2_groups[l2a][:2]
                reps_b = l2_groups[l2b][:2]
                for pa in reps_a:
                    for pb in reps_b:
                        if pa.sku == pb.sku:
                            continue
                        desc = f"同品类互补: {cat} > {l2a} ↔ {l2b}"
                        _add_relation(pa.sku, pb.sku, "complementary", 0.6, desc)
                        counts["complementary"] += 1

    # --- 4. same_series — SKU prefix rule ---
    # Products sharing a common SKU prefix (before the last dash+suffix) are
    # likely variants of the same series (e.g. XH0027-1, XH0027-2, XH0027-3).
    sku_prefix_groups: dict[str, list[str]] = {}
    for prod in enriched:
        prefix = _extract_sku_prefix(prod.sku)
        if prefix:
            sku_prefix_groups.setdefault(prefix, []).append(prod.sku)

    for prefix, skus in sku_prefix_groups.items():
        if len(skus) < 2:
            continue
        for s1, s2 in combinations(skus[:30], 2):  # cap to avoid explosion
            desc = f"同系列产品: {prefix}系列"
            _add_relation(s1, s2, "same_series", 0.8, desc)
            _add_relation(s2, s1, "same_series", 0.8, desc)
            counts["same_series"] += 2

    # --- 5. upgrade_to — same SKU prefix with version/variant differences ---
    # Products sharing a prefix where names suggest newer versions (e.g., "V2", "二代", "新版")
    upgrade_keywords = ["v2", "v3", "二代", "三代", "新版", "升级", "改进", "pro", "plus", "max"]
    for prefix, skus in sku_prefix_groups.items():
        if len(skus) < 2:
            continue
        # Find products with upgrade keywords in name
        for sku in skus[:20]:
            enriched_item = by_sku.get(sku)
            if not enriched_item:
                continue
            name_lower = enriched_item.name.lower()
            has_upgrade = any(kw in name_lower for kw in upgrade_keywords)
            if not has_upgrade:
                continue
            # Link to other products in the same series without upgrade keywords
            for other_sku in skus[:20]:
                if other_sku == sku:
                    continue
                other_item = by_sku.get(other_sku)
                if not other_item:
                    continue
                other_name = other_item.name.lower()
                other_has_upgrade = any(kw in other_name for kw in upgrade_keywords)
                if not other_has_upgrade:
                    desc = f"升级关系: {enriched_item.name} → {other_item.name}"
                    _add_relation(other_sku, sku, "upgrade_to", 0.75, desc)
                    counts["upgrade_to"] += 1

    # --- 6. consumes_with — same brand + category, complementary product types ---
    # E.g., implant (equipment) + abutment (accessory) from same brand
    for (cat, brand), group in cat_brand_groups.items():
        if len(group) < 2 or len(group) > 30:
            continue
        # Find equipment + consumable/accessory pairs
        equipment = [p for p in group if p.product_type in ("equipment",)]
        consumables_acc = [p for p in group if p.product_type in ("consumable", "accessory", "material")]
        for eq in equipment[:5]:
            for con in consumables_acc[:5]:
                if eq.sku == con.sku:
                    continue
                desc = f"配套使用: {eq.name} + {con.name}"
                _add_relation(eq.sku, con.sku, "consumes_with", 0.7, desc)
                counts["consumes_with"] += 1

    # --- 7. co_purchased — purchase co-occurrence ---
    # Products frequently bought together by the same user on the same date.
    purchase_result = await db.execute(
        select(UserPurchase.user_id, UserPurchase.sku, UserPurchase.purchase_date)
    )
    purchase_rows = purchase_result.all()

    # Group purchases by (user_id, purchase_date)
    purchase_groups: dict[tuple[str, Any], list[str]] = {}
    for uid, sku, pdate in purchase_rows:
        if sku:
            key = (uid, pdate)
            purchase_groups.setdefault(key, []).append(sku)

    # Count co-occurrences
    co_occur: Counter[tuple[str, str]] = Counter()
    for skus in purchase_groups.values():
        unique_skus = list(set(skus))
        if len(unique_skus) < 2 or len(unique_skus) > 20:
            continue  # skip single-item or huge batches
        for s1, s2 in combinations(sorted(unique_skus), 2):
            co_occur[(s1, s2)] += 1

    # Create relations for co-occurring pairs (threshold: 2+ times)
    for (s1, s2), count in co_occur.most_common(500):
        if count < 2:
            break
        weight = min(0.5 + 0.1 * (count - 2), 0.9)
        desc = f"共现采购{count}次"
        _add_relation(s1, s2, "co_purchased", weight, desc)
        counts["co_purchased"] += 1

    total = sum(counts.values())
    await db.commit()

    logger.info("Built product relations: %s (total=%d)", counts, total)
    return {**counts, "total": total}


def _extract_sku_prefix(sku: str) -> str | None:
    """Extract the series prefix from a SKU.

    Examples:
        XH0027-1 → XH0027
        VZ008888 → VZ008888 (no dash, returns full SKU — won't group unless identical)
        XH0176US- → XH0176US
    """
    if not sku:
        return None
    # Match pattern: letters+digits optionally followed by -something
    m = re.match(r"^([A-Za-z]+\d+)", sku)
    return m.group(1) if m else None


def _find_sku_by_name(name: str, by_sku: dict[str, ProductEnriched]) -> str | None:
    """Try to find a SKU by matching product name (exact or substring)."""
    name_lower = name.strip().lower()
    if not name_lower:
        return None

    # Exact match first
    for sku, prod in by_sku.items():
        if prod.name.strip().lower() == name_lower:
            return sku

    # Substring match
    for sku, prod in by_sku.items():
        prod_name = prod.name.strip().lower()
        if name_lower in prod_name or prod_name in name_lower:
            return sku

    return None
