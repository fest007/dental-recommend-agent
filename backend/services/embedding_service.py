"""
Embedding service.

Generates product embeddings via OpenAI-compatible API and stores/retrieves
them using Qdrant (embedded mode, single-file).

Falls back gracefully when Qdrant is not installed or the collection doesn't
exist yet.
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Qdrant collection name
COLLECTION = "products"
_DATA_DIR = os.environ.get("DENTAL_AGENT_DATA_DIR")
QDRANT_PATH = os.path.join(_DATA_DIR, "data", "qdrant") if _DATA_DIR else os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "qdrant")


def _get_qdrant_client():
    """Lazily create a Qdrant client (embedded mode). Returns None if unavailable."""
    try:
        from qdrant_client import QdrantClient
        os.makedirs(QDRANT_PATH, exist_ok=True)
        return QdrantClient(path=QDRANT_PATH)
    except Exception as exc:
        logger.warning("Qdrant unavailable: %s", exc)
        return None


def _ensure_collection(client, vector_size: int = 1536) -> bool:
    """Ensure the Qdrant collection exists. Returns True if ready."""
    try:
        from qdrant_client.models import Distance, VectorParams
        collections = [c.name for c in client.get_collections().collections]
        if COLLECTION not in collections:
            client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", COLLECTION, vector_size)
        return True
    except Exception as exc:
        logger.warning("Failed to ensure Qdrant collection: %s", exc)
        return False


def generate_embedding(
    text: str,
    sync_client: Any,
    model: str = "text-embedding-3-small",
) -> Optional[list[float]]:
    """Generate an embedding vector for the given text.

    Parameters
    ----------
    text : str
        The text to embed.
    sync_client : Any
        A synchronous OpenAI client.
    model : str
        Embedding model name.

    Returns
    -------
    list[float] or None on failure.
    """
    try:
        response = sync_client.embeddings.create(
            model=model,
            input=text,
        )
        return response.data[0].embedding
    except Exception as exc:
        logger.error("Embedding generation failed: %s", exc)
        return None


def build_product_embedding_text(
    name: str,
    brand: str,
    category_l1: str,
    category_l2: str,
    product_type: str,
    usage_scenario: str,
    keywords: list[str],
) -> str:
    """Build a concise text representation of a product for embedding."""
    parts = [f"商品：{name}"]
    if brand:
        parts.append(f"品牌：{brand}")
    if category_l1 or category_l2:
        parts.append(f"品类：{category_l1} > {category_l2}")
    if product_type:
        parts.append(f"类型：{product_type}")
    if usage_scenario:
        parts.append(f"场景：{usage_scenario}")
    if keywords:
        parts.append(f"关键词：{', '.join(keywords[:5])}")
    return "\n".join(parts)


async def upsert_product_embedding(
    sku: str,
    product_id: int,
    vector: list[float],
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Store a product embedding in Qdrant.

    Returns the point ID (str) on success, None on failure.
    """
    client = _get_qdrant_client()
    if client is None:
        return None

    if not _ensure_collection(client, vector_size=len(vector)):
        return None

    try:
        from qdrant_client.models import PointStruct
        point_id = f"sku_{sku}"
        payload = {"sku": sku, "product_id": product_id}
        if metadata:
            payload.update(metadata)

        client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return point_id
    except Exception as exc:
        logger.error("Qdrant upsert failed for SKU=%s: %s", sku, exc)
        return None


async def search_similar_products(
    query_vector: list[float],
    top_k: int = 10,
    exclude_skus: Optional[set[str]] = None,
) -> list[dict]:
    """Search Qdrant for products similar to the query vector.

    Returns a list of dicts with ``sku``, ``score``, and ``payload``.
    """
    client = _get_qdrant_client()
    if client is None:
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchAny

        query_filter = None
        if exclude_skus:
            query_filter = Filter(
                must_not=[
                    FieldCondition(key="sku", match=MatchAny(any=list(exclude_skus)))
                ]
            )

        results = client.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
        ).points

        return [
            {
                "sku": hit.payload.get("sku", ""),
                "score": hit.score,
                "product_id": hit.payload.get("product_id"),
            }
            for hit in results
            if hit.payload
        ]
    except Exception as exc:
        logger.error("Qdrant search failed: %s", exc)
        return []


async def delete_product_embedding(sku: str) -> bool:
    """Delete a product embedding from Qdrant by SKU.

    Returns True on success, False on failure.
    """
    client = _get_qdrant_client()
    if client is None:
        return False

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        client.delete(
            collection_name=COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="sku", match=MatchValue(value=sku))]
            ),
        )
        return True
    except Exception as exc:
        logger.error("Qdrant delete failed for SKU=%s: %s", sku, exc)
        return False
