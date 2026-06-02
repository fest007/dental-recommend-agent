"""Products router – CRUD, import, and enriched-product endpoints."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func as sa_func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import ProductRaw, ProductEnriched, SkuMapping
from utils.excel_parser import parse_products_excel

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProductRawCreate(BaseModel):
    row_num: int = 0
    sku: str = Field(..., max_length=50)
    product_name: str = Field(..., max_length=500)
    old_sku: str = Field("", max_length=50)
    status: str = Field("active", max_length=50)


class ProductRawUpdate(BaseModel):
    row_num: Optional[int] = None
    sku: Optional[str] = Field(None, max_length=50)
    product_name: Optional[str] = Field(None, max_length=500)
    old_sku: Optional[str] = Field(None, max_length=50)
    status: Optional[str] = Field(None, max_length=50)


class ProductRawOut(BaseModel):
    id: int
    row_num: int
    sku: str
    product_name: str
    old_sku: Optional[str] = ""
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProductEnrichedOut(BaseModel):
    id: int
    product_id: int
    sku: str
    name: str
    brand: str
    category_l1: str
    category_l2: str
    product_type: str
    usage_scenario: str
    keywords: list
    consumables: list
    related_accessories: list
    typical_purchase_cycle_days: Optional[int] = None
    unit_hint: str
    embedding_vector_id: str
    enriched_at: datetime
    enrichment_confidence: float
    llm_model: str

    class Config:
        from_attributes = True


class PaginatedProducts(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ProductRawOut]


class PaginatedEnriched(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ProductEnrichedOut]


class SkuConflict(BaseModel):
    old_sku: str
    new_sku: str
    existing_sku: str


class ImportResult(BaseModel):
    imported: int
    skipped: int
    conflicts: list[SkuConflict] = []


class EnrichRequest(BaseModel):
    product_ids: list[int] = Field(default_factory=list, description="指定商品ID列表，为空则增强所有未增强商品")
    batch_size: int = Field(10000, ge=1, description="最大处理数量，默认全部")


class EnrichResult(BaseModel):
    total: int
    enriched: int
    failed: int


class RelationBuildResult(BaseModel):
    consumable: int
    accessory: int
    same_category: int
    complementary: int
    same_series: int
    co_purchased: int
    total: int


class DeleteResponse(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=PaginatedProducts)
async def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List products with pagination and optional search / status filter."""
    query = select(ProductRaw)
    count_query = select(sa_func.count()).select_from(ProductRaw)

    filters = []
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(
                ProductRaw.sku.ilike(pattern),
                ProductRaw.product_name.ilike(pattern),
            )
        )
    if status:
        filters.append(ProductRaw.status == status)

    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    # Total count.
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Page of items.
    offset = (page - 1) * page_size
    query = query.order_by(ProductRaw.id).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return PaginatedProducts(total=total, page=page, page_size=page_size, items=items)


@router.get("/enriched", response_model=PaginatedEnriched)
async def list_enriched_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List enriched products with pagination."""
    count_query = select(sa_func.count()).select_from(ProductEnriched)
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * page_size
    query = (
        select(ProductEnriched)
        .order_by(ProductEnriched.id)
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    items = result.scalars().all()

    return PaginatedEnriched(total=total, page=page, page_size=page_size, items=items)


@router.get("/enriched/{product_id}", response_model=ProductEnrichedOut)
async def get_enriched_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single enriched product by its id."""
    result = await db.execute(
        select(ProductEnriched).where(ProductEnriched.id == product_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Enriched product not found")
    return item


@router.get("/export")
async def export_products(
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
):
    """Export products as an Excel file."""
    from fastapi.responses import StreamingResponse
    import io
    from openpyxl import Workbook

    stmt = select(ProductRaw)
    if status:
        stmt = stmt.where(ProductRaw.status == status)
    stmt = stmt.order_by(ProductRaw.id)

    result = await db.execute(stmt)
    products = result.scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "商品列表"
    ws.append(["ID", "行号", "SKU", "商品名称", "旧SKU", "状态", "创建时间", "更新时间"])

    for p in products:
        ws.append([
            p.id,
            p.row_num,
            p.sku,
            p.product_name,
            p.old_sku or "",
            p.status,
            p.created_at.isoformat() if p.created_at else "",
            p.updated_at.isoformat() if p.updated_at else "",
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=products_export.xlsx"},
    )


class GraphNode(BaseModel):
    id: str
    name: str
    category: str
    value: int = 1


class GraphLink(BaseModel):
    source: str
    target: str
    relation_type: str
    weight: float
    description: str


class GraphData(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    categories: list[str]


@router.get("/graph", response_model=GraphData)
async def get_product_graph(
    relation_type: Optional[str] = Query(None, description="Filter by relation type"),
    limit: int = Query(500, ge=10, le=2000, description="Max number of relations to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get product relation graph data for visualization.

    Returns nodes (products) and links (relations) suitable for graph rendering.
    """
    from db.models import ProductRelation, ProductRaw

    # Get relations
    stmt = select(ProductRelation)
    if relation_type:
        stmt = stmt.where(ProductRelation.relation_type == relation_type)
    stmt = stmt.order_by(ProductRelation.weight.desc()).limit(limit)

    result = await db.execute(stmt)
    relations = result.scalars().all()

    if not relations:
        return GraphData(nodes=[], links=[], categories=[])

    # Collect all unique SKUs
    all_skus = set()
    for rel in relations:
        all_skus.add(rel.source_sku)
        all_skus.add(rel.target_sku)

    # Get product names
    products_result = await db.execute(
        select(ProductRaw.sku, ProductRaw.product_name).where(ProductRaw.sku.in_(all_skus))
    )
    sku_names = {row[0]: row[1] for row in products_result.all()}

    # Get enriched data for categories
    enriched_result = await db.execute(
        select(ProductEnriched.sku, ProductEnriched.category_l1).where(ProductEnriched.sku.in_(all_skus))
    )
    sku_categories = {row[0]: row[1] or "未分类" for row in enriched_result.all()}

    # Build nodes
    nodes = []
    category_set = set()
    for sku in all_skus:
        category = sku_categories.get(sku, "未分类")
        category_set.add(category)
        nodes.append(GraphNode(
            id=sku,
            name=sku_names.get(sku, sku),
            category=category,
        ))

    # Build links
    links = []
    for rel in relations:
        links.append(GraphLink(
            source=rel.source_sku,
            target=rel.target_sku,
            relation_type=rel.relation_type,
            weight=rel.weight,
            description=rel.description or "",
        ))

    return GraphData(
        nodes=nodes,
        links=links,
        categories=sorted(list(category_set)),
    )


@router.get("/relation-types")
async def get_relation_types(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get all relation types and their counts."""
    from db.models import ProductRelation

    stmt = (
        select(ProductRelation.relation_type, sa_func.count())
        .group_by(ProductRelation.relation_type)
    )
    result = await db.execute(stmt)
    types = {row[0]: row[1] for row in result.all()}

    # Relation type labels
    type_labels = {
        "consumable_of": "消耗品关系",
        "accessory_of": "配件关系",
        "same_category": "同品类关系",
        "complementary": "互补关系",
        "same_series": "同系列关系",
        "upgrade_to": "升级关系",
        "consumes_with": "配套使用",
        "co_purchased": "共现采购",
    }

    return {
        "types": [
            {"value": k, "label": type_labels.get(k, k), "count": v}
            for k, v in sorted(types.items(), key=lambda x: -x[1])
        ],
        "total": sum(types.values()),
    }


@router.get("/{product_id}", response_model=ProductRawOut)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single raw product by id."""
    result = await db.execute(
        select(ProductRaw).where(ProductRaw.id == product_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return item


@router.post("/", response_model=ProductRawOut, status_code=201)
async def create_product(
    payload: ProductRawCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new product with SKU uniqueness check and mapping sync."""
    data = payload.model_dump()
    # Normalize case
    data["sku"] = (data.get("sku") or "").strip().upper()
    if data.get("old_sku"):
        data["old_sku"] = data["old_sku"].strip().upper()
    sku = data["sku"]
    old_sku = (data.get("old_sku") or "").strip()

    # Check SKU uniqueness
    if sku:
        existing = await db.execute(select(ProductRaw).where(ProductRaw.sku == sku))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"SKU {sku} already exists")

    # Check old_sku conflict — same behavior as Excel import
    if old_sku:
        mapping_result = await db.execute(select(SkuMapping).where(SkuMapping.old_sku == old_sku))
        existing_mapping = mapping_result.scalar_one_or_none()
        if existing_mapping and existing_mapping.new_sku != sku:
            raise HTTPException(
                status_code=409,
                detail=f"旧SKU {old_sku} 已映射到 {existing_mapping.new_sku}，无法映射到 {sku}",
            )

    product = ProductRaw(**data)
    db.add(product)

    # Create mapping
    if old_sku and sku:
        db.add(SkuMapping(new_sku=sku, old_sku=old_sku))

    await db.commit()
    await db.refresh(product)
    return product


@router.put("/{product_id}", response_model=ProductRawOut)
async def update_product(
    product_id: int,
    payload: ProductRawUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing product with SKU uniqueness check and mapping lifecycle."""
    result = await db.execute(
        select(ProductRaw).where(ProductRaw.id == product_id)
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = payload.model_dump(exclude_unset=True)
    # Normalize case
    if "sku" in update_data:
        update_data["sku"] = (update_data["sku"] or "").strip().upper()
    if "old_sku" in update_data and update_data["old_sku"]:
        update_data["old_sku"] = update_data["old_sku"].strip().upper()

    sku_before = product.sku
    old_sku_before = (product.old_sku or "").strip()

    # Check SKU uniqueness if SKU is being changed
    if "sku" in update_data and update_data["sku"] != product.sku:
        sku_check = await db.execute(
            select(ProductRaw).where(ProductRaw.sku == update_data["sku"])
        )
        if sku_check.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"SKU {update_data['sku']} already exists")

    # Determine intended new values (before applying to product)
    intended_sku = update_data.get("sku", product.sku)
    new_old_sku = ""
    if "old_sku" in update_data:
        new_old_sku = (update_data["old_sku"] or "").strip()
    else:
        new_old_sku = old_sku_before

    # Apply updates
    for field, value in update_data.items():
        setattr(product, field, value)

    new_sku = product.sku

    # --- Mapping lifecycle ---

    # 1. If SKU changed: update all mappings pointing to old SKU
    if "sku" in update_data and new_sku != sku_before:
        mappings = await db.execute(select(SkuMapping).where(SkuMapping.new_sku == sku_before))
        for m in mappings.scalars().all():
            m.new_sku = new_sku

    # 2. If old_sku changed or cleared: update mapping lifecycle
    if "old_sku" in update_data:
        # Delete old mapping for the previous old_sku
        if old_sku_before:
            old_mappings = await db.execute(select(SkuMapping).where(SkuMapping.old_sku == old_sku_before))
            for m in old_mappings.scalars().all():
                await db.delete(m)

        # Create mapping for new old_sku — conflict if already mapped to another product
        if new_old_sku:
            existing = await db.execute(select(SkuMapping).where(SkuMapping.old_sku == new_old_sku))
            existing_map = existing.scalar_one_or_none()
            if existing_map and existing_map.new_sku != new_sku:
                raise HTTPException(
                    status_code=409,
                    detail=f"旧SKU {new_old_sku} 已映射到 {existing_map.new_sku}，无法重新映射到 {new_sku}",
                )
            elif not existing_map:
                db.add(SkuMapping(new_sku=new_sku, old_sku=new_old_sku))

    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/{product_id}", response_model=DeleteResponse)
async def delete_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a product and cascade clean enriched records, vector index, and relation edges."""
    result = await db.execute(
        select(ProductRaw).where(ProductRaw.id == product_id)
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    product.status = "deleted"

    # Cascade: delete enriched record
    enriched_result = await db.execute(
        select(ProductEnriched).where(ProductEnriched.product_id == product_id)
    )
    enriched_row = enriched_result.scalar_one_or_none()
    if enriched_row:
        # Delete vector embedding from Qdrant
        if enriched_row.embedding_vector_id:
            try:
                from services.embedding_service import delete_product_embedding
                await delete_product_embedding(product.sku)
            except Exception as exc:
                logger.warning("Failed to delete vector embedding for %s: %s", product.sku, exc)
        await db.delete(enriched_row)

    # Cascade: delete product relation edges
    from db.models import ProductRelation
    rel_result = await db.execute(
        select(ProductRelation).where(
            (ProductRelation.source_sku == product.sku)
            | (ProductRelation.target_sku == product.sku)
        )
    )
    for rel in rel_result.scalars().all():
        await db.delete(rel)

    # Cascade: clean up ALL SkuMapping entries for this product
    # Delete mappings where this SKU is the new_sku target
    mapping_result = await db.execute(
        select(SkuMapping).where(SkuMapping.new_sku == product.sku)
    )
    for m in mapping_result.scalars().all():
        await db.delete(m)

    await db.commit()
    return DeleteResponse(ok=True)


class BatchStatusUpdate(BaseModel):
    product_ids: list[int]
    status: str


@router.post("/batch-status", response_model=DeleteResponse)
async def batch_update_status(
    body: BatchStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Batch update product status."""
    if not body.product_ids:
        raise HTTPException(status_code=400, detail="product_ids cannot be empty")

    result = await db.execute(
        select(ProductRaw).where(ProductRaw.id.in_(body.product_ids))
    )
    products = result.scalars().all()

    for product in products:
        product.status = body.status

    await db.commit()
    return DeleteResponse(ok=True)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@router.post("/import", response_model=ImportResult)
async def import_products(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload an Excel file and bulk-insert product rows."""
    file_bytes = await file.read()
    parsed = parse_products_excel(file_bytes)

    if not parsed:
        return ImportResult(imported=0, skipped=0)

    # Collect existing SKUs to detect duplicates.
    skus = [p["sku"] for p in parsed if p.get("sku")]
    if skus:
        existing_result = await db.execute(
            select(ProductRaw.sku).where(ProductRaw.sku.in_(skus))
        )
        existing_skus = {row[0] for row in existing_result.all()}
    else:
        existing_skus = set()

    imported = 0
    skipped = 0

    # Collect all products first, then build SKU mapping with conflict detection
    from utils.sku_mapping import build_sku_mapping
    _, conflicts = build_sku_mapping(parsed)
    conflict_old_skus = {c["old_sku"] for c in conflicts}

    # Track SKUs inserted in THIS loop to catch same-batch duplicates
    inserted_skus: set[str] = set()

    for item in parsed:
        sku = (item.get("sku") or "").strip().upper()
        if not sku or sku in existing_skus or sku in inserted_skus:
            skipped += 1
            continue

        old_sku = (item.get("old_sku") or "").strip().upper()

        product = ProductRaw(
            row_num=item.get("row_num", 0),
            sku=sku,
            product_name=item.get("product_name", ""),
            old_sku=old_sku,
            status=item.get("status", "active"),
        )
        db.add(product)
        inserted_skus.add(sku)

        # Write SkuMapping for old_sku → new_sku, skip conflicts
        if old_sku and old_sku not in conflict_old_skus:
            mapping = SkuMapping(new_sku=sku, old_sku=old_sku)
            db.add(mapping)

        imported += 1

    await db.commit()
    return ImportResult(
        imported=imported,
        skipped=skipped,
        conflicts=[SkuConflict(**c) for c in conflicts],
    )


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

@router.post("/enrich", response_model=EnrichResult)
async def enrich_products(
    body: EnrichRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trigger LLM enrichment for products via LangGraph pipeline.

    Each product goes through: llm_enrich → build_embed → gen_embedding
    → upsert_vector → save_db. All steps traced by LangSmith.
    """
    from services import llm_config_service
    from services.enrichment_graph import enrich_single_product
    from openai import OpenAI

    config = await llm_config_service.get_config()
    if not config.get("api_key"):
        raise HTTPException(status_code=400, detail="请先在系统设置中配置LLM API Key")

    sync_client = OpenAI(base_url=config["base_url"], api_key=config["api_key"])
    model = config.get("enrichment_model", "gpt-4o-mini")
    embedding_model = config.get("embedding_model", "text-embedding-3-small")

    # Determine which products to enrich
    if body.product_ids:
        result = await db.execute(
            select(ProductRaw).where(ProductRaw.id.in_(body.product_ids))
        )
    else:
        enriched_ids_result = await db.execute(select(ProductEnriched.product_id))
        enriched_ids = {row[0] for row in enriched_ids_result.all()}
        result = await db.execute(
            select(ProductRaw).where(
                ProductRaw.status.notin_(["deleted", "说明SKU"]),
                ProductRaw.id.notin_(enriched_ids) if enriched_ids else True,
            ).limit(body.batch_size)
        )

    products = list(result.scalars().all())
    if not products:
        return EnrichResult(total=0, enriched=0, failed=0)

    import asyncio

    async def enrich_one(product):
        return await enrich_single_product(
            product_id=product.id, sku=product.sku,
            product_name=product.product_name, sync_client=sync_client,
            llm_model=model, embedding_model=embedding_model,
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 4096),
            timeout=config.get("timeout", 30),
        )

    # Adaptive concurrency processing
    enriched_count = 0
    failed_count = 0
    concurrency = 20
    idx = 0

    while idx < len(products):
        batch = products[idx:idx + concurrency]
        results = await asyncio.gather(*[enrich_one(p) for p in batch], return_exceptions=True)
        batch_failed = 0
        for r in results:
            if isinstance(r, Exception) or not r.get("success"):
                failed_count += 1
                batch_failed += 1
            else:
                enriched_count += 1

        # Adjust concurrency
        fail_rate = batch_failed / len(batch) if batch else 0
        if fail_rate > 0.5:
            concurrency = max(2, concurrency // 2)
        elif fail_rate > 0:
            concurrency = max(2, int(concurrency * 0.75))
        elif concurrency < 20:
            concurrency = min(20, concurrency + 2)

        idx += len(batch)

    await db.commit()
    return EnrichResult(total=len(products), enriched=enriched_count, failed=failed_count)


@router.post("/enrich-stream")
async def enrich_products_stream(
    body: EnrichRequest,
    db: AsyncSession = Depends(get_db),
):
    """Enrich products with SSE progress streaming.

    Returns a stream of JSON events:
    - {"type": "start", "total": N}
    - {"type": "progress", "current": N, "total": N, "sku": "...", "success": true}
    - {"type": "progress", "current": N, "total": N, "sku": "...", "success": false, "error": "..."}
    - {"type": "done", "enriched": N, "failed": N}
    """
    import json as json_mod
    from fastapi.responses import StreamingResponse
    from services import llm_config_service
    from services.enrichment_graph import enrich_single_product
    from openai import OpenAI

    config = await llm_config_service.get_config()
    if not config.get("api_key"):
        raise HTTPException(status_code=400, detail="请先在系统设置中配置LLM API Key")

    sync_client = OpenAI(base_url=config["base_url"], api_key=config["api_key"])
    model = config.get("enrichment_model", "gpt-4o-mini")
    embedding_model = config.get("embedding_model", "text-embedding-3-small")

    # Determine which products to enrich
    enriched_ids_result = await db.execute(select(ProductEnriched.product_id))
    enriched_ids = {row[0] for row in enriched_ids_result.all()}
    already_enriched = len(enriched_ids)

    if body.product_ids:
        result = await db.execute(
            select(ProductRaw).where(ProductRaw.id.in_(body.product_ids))
        )
    else:
        result = await db.execute(
            select(ProductRaw).where(
                ProductRaw.status.notin_(["deleted", "说明SKU"]),
                ProductRaw.id.notin_(enriched_ids) if enriched_ids else True,
            ).limit(body.batch_size)
        )

    products = list(result.scalars().all())
    total = len(products)

    async def event_stream():
        import asyncio

        yield json_mod.dumps({
            "type": "start",
            "total": total,
            "already_enriched": already_enriched,
        }) + "\n"

        enriched_count = 0
        failed_count = 0
        completed = 0
        concurrency = 20  # start concurrency

        async def enrich_one(product):
            res = await enrich_single_product(
                product_id=product.id, sku=product.sku,
                product_name=product.product_name, sync_client=sync_client,
                llm_model=model, embedding_model=embedding_model,
                temperature=config.get("temperature", 0.7),
                max_tokens=config.get("max_tokens", 4096),
                timeout=config.get("timeout", 30),
            )
            return product, res

        idx = 0
        while idx < total:
            batch = products[idx:idx + concurrency]
            tasks = [asyncio.create_task(enrich_one(p)) for p in batch]
            batch_results = []

            for task in asyncio.as_completed(tasks):
                try:
                    product, res = await task
                except Exception:
                    continue
                completed += 1
                batch_results.append(res["success"])
                if res["success"]:
                    enriched_count += 1
                else:
                    failed_count += 1
                yield json_mod.dumps({
                    "type": "progress", "current": completed, "total": total,
                    "sku": product.sku, "product_name": product.product_name,
                    "success": res["success"], "error": res.get("error", ""),
                    "concurrency": concurrency,
                }) + "\n"

            # Adaptive concurrency: adjust based on batch failure rate
            batch_size = len(batch)
            batch_failed = batch_size - sum(batch_results)
            if batch_size > 0:
                fail_rate = batch_failed / batch_size
                old = concurrency
                if fail_rate > 0.5:
                    concurrency = max(2, concurrency // 2)
                elif fail_rate > 0:
                    concurrency = max(2, int(concurrency * 0.75))
                elif concurrency < 20:
                    concurrency = min(20, concurrency + 2)
                if concurrency != old:
                    yield json_mod.dumps({
                        "type": "concurrency_change",
                        "old": old,
                        "new": concurrency,
                        "reason": f"batch fail rate {fail_rate:.0%}",
                    }) + "\n"

            idx += len(batch)

        await db.commit()

        yield json_mod.dumps({
            "type": "done",
            "total": total,
            "enriched": enriched_count,
            "failed": failed_count,
        }) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Relation graph
# ---------------------------------------------------------------------------

@router.post("/build-relations", response_model=RelationBuildResult)
async def build_product_relations(
    clear_existing: bool = Query(False, description="是否清除已有关系后重建"),
    db: AsyncSession = Depends(get_db),
):
    """Build product_relations from enriched product data.

    Creates:
    - consumable_of / accessory_of relations from LLM-enriched fields
    - same_category relations (same brand + category)
    - complementary relations (same top category, different sub category)
    """
    from services.relation_builder import build_relations
    result = await build_relations(db, clear_existing=clear_existing)
    return RelationBuildResult(**result)
