"""
User profile computation service.

Aggregates a user's purchase history into a structured profile JSON
matching the schema defined in tech_design.md (section 4.3).
"""

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ProductEnriched, UserPurchase, UserProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_purchase_cycles(
    purchases: list[dict],
) -> dict[str, dict[str, Any]]:
    """Group purchases by category (from enriched data) and compute average
    purchase interval in days for each category.

    Returns::

        {
            "根管治疗": {"avg_days": 45, "last_purchase_date": "2026-06-01", "count": 5},
            ...
        }
    """
    category_dates: dict[str, list[date]] = defaultdict(list)
    for p in purchases:
        cat = p.get("category_l1") or "未分类"
        pd = p.get("purchase_date")
        if isinstance(pd, str):
            pd = date.fromisoformat(pd)
        if isinstance(pd, date):
            category_dates[cat].append(pd)

    result: dict[str, dict[str, Any]] = {}
    for cat, dates in category_dates.items():
        dates_sorted = sorted(dates)
        if len(dates_sorted) >= 2:
            intervals = [
                (dates_sorted[i + 1] - dates_sorted[i]).days
                for i in range(len(dates_sorted) - 1)
            ]
            avg_days = round(sum(intervals) / len(intervals))
        else:
            avg_days = None
        result[cat] = {
            "avg_days": avg_days,
            "last_purchase_date": dates_sorted[-1].isoformat(),
            "count": len(dates_sorted),
        }
    return result


def _detect_consumable_alerts(
    purchases: list[dict],
    enriched_map: dict[str, dict],
) -> list[dict]:
    """Identify consumable items that may need replacement based on
    typical_purchase_cycle_days from enriched data.

    Returns a list of alert dicts.
    """
    alerts: list[dict] = []
    # Group purchases by SKU, find the most recent purchase date per SKU
    sku_last: dict[str, dict] = {}
    for p in purchases:
        sku = p.get("sku", "")
        pd = p.get("purchase_date")
        if isinstance(pd, str):
            pd = date.fromisoformat(pd)
        if not isinstance(pd, date):
            continue
        if sku not in sku_last or pd > date.fromisoformat(sku_last[sku]["last_purchased"]):
            sku_last[sku] = {
                "product_name": p.get("product_name", ""),
                "last_purchased": pd.isoformat(),
            }

    today = date.today()
    for sku, info in sku_last.items():
        enriched = enriched_map.get(sku)
        if not enriched:
            continue
        cycle_days = enriched.get("typical_purchase_cycle_days")
        if cycle_days is None or cycle_days <= 0:
            continue
        product_type = enriched.get("product_type", "")
        # Only flag consumables / accessories
        if product_type not in ("consumable", "accessory", "reagent", "material"):
            continue
        last = date.fromisoformat(info["last_purchased"])
        days_since = (today - last).days
        expected_replacement = last.replace(day=last.day)  # same day
        # Simple: add cycle_days to last purchase
        try:
            from datetime import timedelta
            expected_replacement = last + timedelta(days=cycle_days)
        except Exception:
            expected_replacement = last

        status = "overdue" if today > expected_replacement else "upcoming"
        # Only include if overdue or within 7 days
        days_until = (expected_replacement - today).days
        if days_until > 7:
            continue

        alerts.append({
            "product_name": info["product_name"],
            "sku": sku,
            "related_device": enriched.get("category_l2", ""),
            "last_purchased": info["last_purchased"],
            "expected_replacement": expected_replacement.isoformat(),
            "days_overdue": max(0, -days_until),
            "status": status,
        })

    return alerts


def _compute_recency_score(purchases: list[dict]) -> float:
    """Score 0-1 based on how recently the user purchased.

    1.0 = purchased today, 0.0 = no purchases in the last 365 days.
    """
    today = date.today()
    most_recent: Optional[date] = None
    for p in purchases:
        pd = p.get("purchase_date")
        if isinstance(pd, str):
            pd = date.fromisoformat(pd)
        if isinstance(pd, date):
            if most_recent is None or pd > most_recent:
                most_recent = pd
    if most_recent is None:
        return 0.0
    days_ago = (today - most_recent).days
    if days_ago <= 0:
        return 1.0
    if days_ago >= 365:
        return 0.0
    return round(1.0 - (days_ago / 365), 2)


def _compute_value_tier(
    total_records: int,
    unique_skus: int,
    purchase_dates: int,
    purchase_span_days: int,
) -> str:
    """Classify the user into a value tier based on purchase activity."""
    # Simple heuristic
    if total_records >= 100 and purchase_span_days >= 365:
        return "high"
    if total_records >= 30 or (purchase_dates >= 10 and purchase_span_days >= 180):
        return "medium"
    return "low"


def _infer_customer_type(purchases: list[dict], enriched_map: dict[str, dict]) -> str:
    """Infer whether the user is a clinic or distributor."""
    # Simple heuristic: if average quantity per purchase is high and
    # categories are diverse, likely a distributor.
    if not purchases:
        return "未知"
    total_qty = sum(p.get("quantity", 1) for p in purchases)
    avg_qty = total_qty / len(purchases)
    unique_cats = set()
    for p in purchases:
        sku = p.get("sku", "")
        enriched = enriched_map.get(sku, {})
        cat = enriched.get("category_l1", "")
        if cat:
            unique_cats.add(cat)

    if avg_qty >= 50 and len(unique_cats) >= 5:
        return "经销商用户"
    if avg_qty >= 20 and len(unique_cats) >= 3:
        return "诊所用户（推断）"
    return "诊所用户（推断）"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def compute_profile(user_id: str, db: AsyncSession) -> dict:
    """Compute and store a user profile from their purchase history.

    Parameters
    ----------
    user_id : str
        The user/customer ID.
    db : AsyncSession
        Database session.

    Returns
    -------
    dict
        The computed profile JSON.
    """
    # ------------------------------------------------------------------
    # 1. Fetch all purchases for this user
    # ------------------------------------------------------------------
    stmt = (
        select(UserPurchase)
        .where(UserPurchase.user_id == user_id)
        .order_by(UserPurchase.purchase_date)
    )
    result = await db.execute(stmt)
    purchase_rows = result.scalars().all()

    purchases: list[dict] = []
    for row in purchase_rows:
        purchases.append({
            "id": row.id,
            "user_id": row.user_id,
            "sku": row.sku,
            "product_name": row.product_name,
            "quantity": row.quantity,
            "purchase_date": row.purchase_date,
            "original_sku": row.original_sku,
        })

    if not purchases:
        logger.info("No purchases found for user %s", user_id)
        empty_profile = {
            "user_id": user_id,
            "profile_generated_at": date.today().isoformat(),
            "basic_info": {},
            "purchase_summary": {
                "total_records": 0,
                "unique_skus": 0,
                "purchase_dates": 0,
                "avg_records_per_date": 0,
            },
            "category_preference": [],
            "brand_preference": [],
            "purchase_cycle": {},
            "consumable_alerts": [],
            "recency_score": 0.0,
            "value_tier": "low",
        }
        await _store_profile(user_id, empty_profile, db)
        return empty_profile

    # ------------------------------------------------------------------
    # 2. Fetch enriched data for purchased SKUs
    # ------------------------------------------------------------------
    purchased_skus = list({p["sku"] for p in purchases})
    enriched_stmt = select(ProductEnriched).where(
        ProductEnriched.sku.in_(purchased_skus)
    )
    enriched_result = await db.execute(enriched_stmt)
    enriched_rows = enriched_result.scalars().all()

    enriched_map: dict[str, dict] = {}
    for row in enriched_rows:
        enriched_map[row.sku] = {
            "brand": row.brand,
            "category_l1": row.category_l1,
            "category_l2": row.category_l2,
            "product_type": row.product_type,
            "usage_scenario": row.usage_scenario,
            "keywords": row.keywords or [],
            "consumables": row.consumables or [],
            "related_accessories": row.related_accessories or [],
            "typical_purchase_cycle_days": row.typical_purchase_cycle_days,
            "unit_hint": row.unit_hint,
        }

    # Merge enriched data into purchases for downstream computation
    for p in purchases:
        enriched = enriched_map.get(p["sku"], {})
        p["category_l1"] = enriched.get("category_l1", "")
        p["brand"] = enriched.get("brand", "")

    # ------------------------------------------------------------------
    # 3. Basic info
    # ------------------------------------------------------------------
    purchase_dates_set: set[str] = set()
    quantities: list[int] = []
    all_dates: list[date] = []
    for p in purchases:
        pd = p["purchase_date"]
        if isinstance(pd, date):
            purchase_dates_set.add(pd.isoformat())
            all_dates.append(pd)
        quantities.append(p.get("quantity", 1))

    all_dates_sorted = sorted(all_dates) if all_dates else []
    first_date = all_dates_sorted[0] if all_dates_sorted else None
    last_date = all_dates_sorted[-1] if all_dates_sorted else None
    span_days = (last_date - first_date).days if first_date and last_date else 0

    customer_type = _infer_customer_type(purchases, enriched_map)

    basic_info = {
        "customer_type": customer_type,
        "purchase_span_days": span_days,
        "first_purchase_date": first_date.isoformat() if first_date else None,
        "last_purchase_date": last_date.isoformat() if last_date else None,
    }

    # ------------------------------------------------------------------
    # 4. Purchase summary
    # ------------------------------------------------------------------
    unique_skus_set = {p["sku"] for p in purchases}
    avg_per_date = round(len(purchases) / max(len(purchase_dates_set), 1), 1)

    purchase_summary = {
        "total_records": len(purchases),
        "unique_skus": len(unique_skus_set),
        "purchase_dates": len(purchase_dates_set),
        "avg_records_per_date": avg_per_date,
    }

    # ------------------------------------------------------------------
    # 5. Category preference
    # ------------------------------------------------------------------
    category_counter: Counter = Counter()
    for p in purchases:
        cat = p.get("category_l1") or "未分类"
        category_counter[cat] += 1

    total_purchases = len(purchases)
    category_preference = [
        {
            "category": cat,
            "count": cnt,
            "ratio": round(cnt / total_purchases, 2),
        }
        for cat, cnt in category_counter.most_common()
    ]

    # ------------------------------------------------------------------
    # 6. Brand preference
    # ------------------------------------------------------------------
    brand_counter: Counter = Counter()
    for p in purchases:
        brand = p.get("brand") or "未知"
        brand_counter[brand] += 1

    brand_preference = [
        {
            "brand": brand,
            "count": cnt,
            "ratio": round(cnt / total_purchases, 2),
        }
        for brand, cnt in brand_counter.most_common()
    ]

    # ------------------------------------------------------------------
    # 7. Purchase cycle
    # ------------------------------------------------------------------
    purchase_cycle = _compute_purchase_cycles(purchases)

    # ------------------------------------------------------------------
    # 8. Consumable alerts
    # ------------------------------------------------------------------
    consumable_alerts = _detect_consumable_alerts(purchases, enriched_map)

    # ------------------------------------------------------------------
    # 9. Recency score & value tier
    # ------------------------------------------------------------------
    recency_score = _compute_recency_score(purchases)
    value_tier = _compute_value_tier(
        len(purchases), len(unique_skus_set), len(purchase_dates_set), span_days
    )

    # ------------------------------------------------------------------
    # 10. Assemble profile
    # ------------------------------------------------------------------
    profile: dict[str, Any] = {
        "user_id": user_id,
        "profile_generated_at": date.today().isoformat(),
        "basic_info": basic_info,
        "purchase_summary": purchase_summary,
        "category_preference": category_preference,
        "brand_preference": brand_preference,
        "purchase_cycle": purchase_cycle,
        "consumable_alerts": consumable_alerts,
        "recency_score": recency_score,
        "value_tier": value_tier,
    }

    # ------------------------------------------------------------------
    # 11. Store
    # ------------------------------------------------------------------
    await _store_profile(user_id, profile, db)

    logger.info("Profile computed for user %s: %d purchases, %d SKUs", user_id, len(purchases), len(unique_skus_set))
    return profile


async def _store_profile(user_id: str, profile: dict, db: AsyncSession) -> None:
    """Insert or update the user_profiles table."""
    stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        row = UserProfile(user_id=user_id, profile_json=profile)
        db.add(row)
    else:
        row.profile_json = profile
        row.updated_at = datetime.now(timezone.utc)

    await db.commit()
