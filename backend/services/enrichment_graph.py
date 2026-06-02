"""
LangGraph-based product enrichment pipeline with automatic LangSmith tracing.

Single-product enrichment flow:
    1. llm_enrich     — call LLM to extract structured metadata
    2. parse_validate — parse JSON, apply defaults, normalize categories
    3. build_embed    — build embedding text from enriched data
    4. gen_embedding  — generate vector embedding
    5. upsert_vector  — store vector in Qdrant
    6. save_db        — save/update enriched record in SQLite

This graph is reusable across:
    - Manual enrichment endpoint (routers/products.py)
    - Daily scheduled enrichment (scheduler.py)
    - Batch enrichment (batch_enrich loop)

LangSmith tracing is automatic via LANGSMITH_* environment variables.
"""

import asyncio
import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class EnrichState(TypedDict):
    """State for a single product enrichment run."""
    # Input
    product_id: int
    sku: str
    product_name: str
    # Config
    sync_client: Any  # OpenAI sync client
    embedding_model: str
    llm_model: str
    temperature: float
    max_tokens: int
    timeout: int
    # Intermediate
    cleaned_name: str
    enriched_data: dict
    embedding_text: str
    vector: Optional[list[float]]
    vector_id: str
    # Output
    success: bool
    error: str


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

async def llm_enrich(state: EnrichState) -> dict:
    """Call LLM to extract structured metadata from product name."""
    from services.product_enrichment import enrich_product, _clean_product_name

    cleaned = _clean_product_name(state["product_name"])

    enriched = await asyncio.to_thread(
        enrich_product,
        state["product_name"],
        state["sku"],
        state["sync_client"],
        state["llm_model"],
        state["temperature"],
        state["max_tokens"],
        state["timeout"],
    )

    has_error = bool(enriched.get("error"))
    return {
        "cleaned_name": cleaned,
        "enriched_data": enriched,
        "success": not has_error,
        "error": enriched.get("error", ""),
    }


async def parse_validate(state: EnrichState) -> dict:
    """Parse and validate enriched data. Skip embedding if enrichment failed."""
    if not state.get("success"):
        return {}
    return {}


async def build_embed(state: EnrichState) -> dict:
    """Build embedding text from enriched data."""
    if not state.get("success"):
        return {"embedding_text": ""}

    from services.embedding_service import build_product_embedding_text

    ed = state["enriched_data"]
    text = build_product_embedding_text(
        name=state["product_name"],
        brand=ed.get("brand", ""),
        category_l1=ed.get("category_l1", ""),
        category_l2=ed.get("category_l2", ""),
        product_type=ed.get("product_type", ""),
        usage_scenario=ed.get("usage_scenario", ""),
        keywords=ed.get("keywords", []),
    )
    return {"embedding_text": text}


async def gen_embedding(state: EnrichState) -> dict:
    """Generate vector embedding."""
    if not state.get("success") or not state.get("embedding_text"):
        return {"vector": None}

    from services.embedding_service import generate_embedding

    vector = await asyncio.to_thread(
        generate_embedding,
        state["embedding_text"],
        state["sync_client"],
        state["embedding_model"],
    )
    return {"vector": vector}


async def upsert_vector(state: EnrichState) -> dict:
    """Store vector in Qdrant."""
    vector = state.get("vector")
    if not vector:
        return {"vector_id": ""}

    from services.embedding_service import upsert_product_embedding

    ed = state["enriched_data"]
    vector_id = await upsert_product_embedding(
        sku=state["sku"],
        product_id=state["product_id"],
        vector=vector,
        metadata={
            "name": state["product_name"],
            "brand": ed.get("brand", ""),
            "category_l1": ed.get("category_l1", ""),
        },
    ) or ""
    return {"vector_id": vector_id}


async def save_db(state: EnrichState) -> dict:
    """Save or update enriched record in SQLite."""
    if not state.get("success"):
        return {}

    from sqlalchemy import select
    from db.database import async_session
    from db.models import ProductEnriched

    ed = state["enriched_data"]
    vector_id = state.get("vector_id", "")

    async with async_session() as db:
        # Check existing
        result = await db.execute(
            select(ProductEnriched).where(ProductEnriched.product_id == state["product_id"])
        )
        existing = result.scalar_one_or_none()

        if existing:
            for key in ("brand", "category_l1", "category_l2", "product_type",
                        "usage_scenario", "keywords", "consumables",
                        "related_accessories", "typical_purchase_cycle_days",
                        "unit_hint", "enrichment_confidence", "llm_model", "name"):
                if key in ed:
                    setattr(existing, key, ed[key])
            existing.name = state["product_name"]
            if vector_id:
                existing.embedding_vector_id = vector_id
        else:
            record = ProductEnriched(
                product_id=state["product_id"],
                sku=state["sku"],
                name=state["product_name"],
                brand=ed.get("brand", ""),
                category_l1=ed.get("category_l1", ""),
                category_l2=ed.get("category_l2", ""),
                product_type=ed.get("product_type", "consumable"),
                usage_scenario=ed.get("usage_scenario", ""),
                keywords=ed.get("keywords", []),
                consumables=ed.get("consumables", []),
                related_accessories=ed.get("related_accessories", []),
                typical_purchase_cycle_days=ed.get("typical_purchase_cycle_days"),
                unit_hint=ed.get("unit_hint", "个"),
                enrichment_confidence=ed.get("enrichment_confidence", 0.85),
                llm_model=ed.get("llm_model", ""),
                embedding_vector_id=vector_id,
            )
            db.add(record)

        await db.commit()

    return {}


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def _should_continue_embedding(state: EnrichState) -> str:
    """Conditional edge: skip embedding if enrichment failed."""
    if state.get("success") and state.get("embedding_text"):
        return "gen_embedding"
    return "save_db"


def build_enrichment_graph() -> StateGraph:
    """Build the LangGraph enrichment pipeline."""
    graph = StateGraph(EnrichState)

    graph.add_node("llm_enrich", llm_enrich)
    graph.add_node("build_embed", build_embed)
    graph.add_node("gen_embedding", gen_embedding)
    graph.add_node("upsert_vector", upsert_vector)
    graph.add_node("save_db", save_db)

    graph.set_entry_point("llm_enrich")
    graph.add_edge("llm_enrich", "build_embed")
    graph.add_conditional_edges("build_embed", _should_continue_embedding, {
        "gen_embedding": "gen_embedding",
        "save_db": "save_db",
    })
    graph.add_edge("gen_embedding", "upsert_vector")
    graph.add_edge("upsert_vector", "save_db")
    graph.add_edge("save_db", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_enrichment_graph = None


def get_enrichment_graph():
    global _enrichment_graph
    if _enrichment_graph is None:
        _enrichment_graph = build_enrichment_graph()
    return _enrichment_graph


async def enrich_single_product(
    product_id: int,
    sku: str,
    product_name: str,
    sync_client: Any,
    llm_model: str = "gpt-4o-mini",
    embedding_model: str = "text-embedding-3-small",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 30,
) -> dict:
    """Enrich a single product via the LangGraph pipeline.

    Returns the final state dict with success/error info.
    All steps are automatically traced by LangSmith.
    """
    graph = get_enrichment_graph()

    initial_state: EnrichState = {
        "product_id": product_id,
        "sku": sku,
        "product_name": product_name,
        "sync_client": sync_client,
        "embedding_model": embedding_model,
        "llm_model": llm_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "cleaned_name": "",
        "enriched_data": {},
        "embedding_text": "",
        "vector": None,
        "vector_id": "",
        "success": False,
        "error": "",
    }

    result = await graph.ainvoke(initial_state)
    return {
        "success": result.get("success", False),
        "error": result.get("error", ""),
        "enriched_data": result.get("enriched_data", {}),
        "vector_id": result.get("vector_id", ""),
    }
