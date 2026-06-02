"""Purchases router – CRUD and Excel import with SKU mapping."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import UserPurchase, SkuMapping
from utils.excel_parser import parse_purchases_excel
from utils.sku_mapping import build_sku_mapping, standardize_purchase

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PurchaseCreate(BaseModel):
    user_id: str = Field(..., max_length=50)
    sku: str = Field(..., max_length=50)
    product_name: str = Field(..., max_length=500)
    quantity: int = Field(1, ge=1)
    purchase_date: date
    original_sku: str = Field("", max_length=50)


class PurchaseUpdate(BaseModel):
    user_id: Optional[str] = Field(None, max_length=50)
    sku: Optional[str] = Field(None, max_length=50)
    product_name: Optional[str] = Field(None, max_length=500)
    quantity: Optional[int] = Field(None, ge=1)
    purchase_date: Optional[date] = None
    original_sku: Optional[str] = Field(None, max_length=50)


class PurchaseOut(BaseModel):
    id: int
    user_id: str
    sku: str
    product_name: str
    quantity: int
    purchase_date: date
    original_sku: str
    imported_at: datetime

    class Config:
        from_attributes = True


class PaginatedPurchases(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PurchaseOut]


class ImportResult(BaseModel):
    imported: int
    skipped: int
    note: Optional[str] = None


class DeleteResponse(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=PaginatedPurchases)
async def list_purchases(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user_id: Optional[str] = None,
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """List purchases with pagination, optionally filtered by user_id and date range."""
    query = select(UserPurchase)
    count_query = select(sa_func.count()).select_from(UserPurchase)

    if user_id:
        user_id = user_id.strip().upper()
        query = query.where(UserPurchase.user_id == user_id)
        count_query = count_query.where(UserPurchase.user_id == user_id)

    if date_from:
        try:
            from_date = date.fromisoformat(date_from)
            query = query.where(UserPurchase.purchase_date >= from_date)
            count_query = count_query.where(UserPurchase.purchase_date >= from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = date.fromisoformat(date_to)
            query = query.where(UserPurchase.purchase_date <= to_date)
            count_query = count_query.where(UserPurchase.purchase_date <= to_date)
        except ValueError:
            pass

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * page_size
    query = query.order_by(UserPurchase.id.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return PaginatedPurchases(total=total, page=page, page_size=page_size, items=items)


@router.post("/", response_model=PurchaseOut, status_code=201)
async def create_purchase(
    payload: PurchaseCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new purchase record with SKU standardization."""
    # Load SKU mapping for old→new conversion
    mapping_result = await db.execute(select(SkuMapping))
    sku_map = {m.old_sku: m.new_sku for m in mapping_result.scalars().all() if m.old_sku and m.new_sku}

    data = payload.model_dump()
    # Normalize case
    data["user_id"] = (data.get("user_id") or "").strip().upper()
    data["sku"] = (data.get("sku") or "").strip().upper()

    standardized = standardize_purchase(data, sku_map)
    data["sku"] = standardized["sku"]
    data["original_sku"] = standardized.get("original_sku", "")

    purchase = UserPurchase(**data)
    db.add(purchase)
    await db.commit()
    await db.refresh(purchase)

    # Refresh user profile (non-fatal: purchase data is already committed)
    try:
        from services.user_profile import compute_profile
        await compute_profile(data["user_id"], db)
    except Exception as exc:
        logger.warning("Profile refresh failed after create_purchase for %s: %s", data["user_id"], exc)

    return purchase


@router.put("/{purchase_id}", response_model=PurchaseOut)
async def update_purchase(
    purchase_id: int,
    payload: PurchaseUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing purchase (partial update) with SKU standardization."""
    result = await db.execute(
        select(UserPurchase).where(UserPurchase.id == purchase_id)
    )
    purchase = result.scalar_one_or_none()
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")

    update_data = payload.model_dump(exclude_unset=True)

    # Normalize case
    if "user_id" in update_data:
        update_data["user_id"] = (update_data["user_id"] or "").strip().upper()
    if "sku" in update_data:
        update_data["sku"] = (update_data["sku"] or "").strip().upper()

    # Standardize SKU if it's being updated
    if "sku" in update_data:
        mapping_result = await db.execute(select(SkuMapping))
        sku_map = {m.old_sku: m.new_sku for m in mapping_result.scalars().all() if m.old_sku and m.new_sku}
        standardized = standardize_purchase({"sku": update_data["sku"], "original_sku": ""}, sku_map)
        update_data["sku"] = standardized["sku"]
        update_data["original_sku"] = standardized.get("original_sku", "")

    for field, value in update_data.items():
        setattr(purchase, field, value)

    await db.commit()
    await db.refresh(purchase)

    # Refresh user profile (non-fatal)
    try:
        from services.user_profile import compute_profile
        await compute_profile(purchase.user_id, db)
    except Exception as exc:
        logger.warning("Profile refresh failed after update_purchase for %s: %s", purchase.user_id, exc)

    return purchase


@router.delete("/{purchase_id}", response_model=DeleteResponse)
async def delete_purchase(
    purchase_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a purchase record."""
    result = await db.execute(
        select(UserPurchase).where(UserPurchase.id == purchase_id)
    )
    purchase = result.scalar_one_or_none()
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")

    user_id = purchase.user_id
    await db.delete(purchase)
    await db.commit()

    # Refresh user profile (non-fatal)
    try:
        from services.user_profile import compute_profile
        await compute_profile(user_id, db)
    except Exception as exc:
        logger.warning("Profile refresh failed after delete_purchase for %s: %s", user_id, exc)

    return DeleteResponse(ok=True)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@router.post("/import", response_model=ImportResult)
async def import_purchases(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload an Excel file, parse purchase rows, apply SKU mapping, and bulk insert.

    Idempotent: the same file uploaded twice will not create duplicate records.
    Different files with same-day-same-SKU rows are treated as separate purchases.
    """
    file_bytes = await file.read()

    # File fingerprint for idempotent re-import
    file_hash = hashlib.sha256(file_bytes).hexdigest()[:32]

    # Check if this exact file was already imported
    existing_batch = await db.execute(
        select(sa_func.count()).select_from(UserPurchase).where(
            UserPurchase.import_batch == file_hash
        )
    )
    if existing_batch.scalar_one() > 0:
        return ImportResult(
            imported=0,
            skipped=len(parse_purchases_excel(file_bytes)),
            note="此文件已导入过，跳过（如需重新导入请先删除旧数据）",
        )

    parsed = parse_purchases_excel(file_bytes)
    if not parsed:
        return ImportResult(imported=0, skipped=0)

    # Load the current SKU mapping from the database (populated by product import).
    mapping_result = await db.execute(select(SkuMapping))
    db_mappings = mapping_result.scalars().all()
    sku_map: dict[str, str] = {m.old_sku: m.new_sku for m in db_mappings if m.old_sku and m.new_sku}

    imported = 0
    skipped = 0
    affected_users: set[str] = set()
    for raw_purchase in parsed:
        standardized = standardize_purchase(raw_purchase, sku_map)

        # Skip rows that are completely empty (no user_id and no sku).
        if not standardized.get("user_id") and not standardized.get("sku"):
            skipped += 1
            continue

        purchase = UserPurchase(
            user_id=standardized.get("user_id", ""),
            sku=standardized.get("sku", ""),
            product_name=standardized.get("product_name", ""),
            quantity=standardized.get("quantity", 1),
            purchase_date=standardized.get("purchase_date", date.today()),
            original_sku=standardized.get("original_sku", ""),
            import_batch=file_hash,
        )
        db.add(purchase)
        imported += 1
        affected_users.add(purchase.user_id)

    await db.commit()

    # Refresh profiles for all affected users (non-fatal)
    if affected_users:
        from services.user_profile import compute_profile
        for uid in affected_users:
            try:
                await compute_profile(uid, db)
            except Exception as exc:
                logger.warning("Profile refresh failed after import for user %s: %s", uid, exc)

    return ImportResult(imported=imported, skipped=skipped)
