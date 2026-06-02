"""
Settings router -- LLM configuration management endpoints.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import LlmConfig
from services import llm_config_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class LlmConfigResponse(BaseModel):
    base_url: str
    api_key: str  # masked before returning
    ranking_model: str
    enrichment_model: str
    embedding_model: str
    temperature: float
    max_tokens: int
    timeout: int
    langsmith_api_key: str  # masked
    langsmith_project: str
    langsmith_enabled: bool


class LlmConfigRequest(BaseModel):
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    ranking_model: str = Field(..., min_length=1)
    enrichment_model: str = Field(..., min_length=1)
    embedding_model: str = Field(..., min_length=1)
    temperature: float = Field(..., ge=0.0, le=2.0)
    max_tokens: int = Field(..., ge=1)
    timeout: int = Field(..., ge=1)
    langsmith_api_key: str = Field("", max_length=200)
    langsmith_project: str = Field("dental-recommend-agent", max_length=100)
    langsmith_enabled: bool = False


class TestConnectionRequest(BaseModel):
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)


class TestConnectionResponse(BaseModel):
    success: bool
    models: list[str]
    error: Optional[str] = None


class ModelInfo(BaseModel):
    id: str


class ModelsResponse(BaseModel):
    models: list[str]
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask_api_key(key: str) -> str:
    """Show the first 6 and last 4 characters only."""
    if not key or len(key) <= 10:
        return key
    return f"{key[:6]}...{key[-4:]}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/llm-config", response_model=LlmConfigResponse)
async def get_llm_config(db: AsyncSession = Depends(get_db)):
    """Return the current LLM configuration with the API key masked."""
    result = await db.execute(select(LlmConfig).where(LlmConfig.id == 1))
    row = result.scalar_one_or_none()

    if row is not None:
        from services.llm_config_service import _decrypt_api_key
        real_key = _decrypt_api_key(row.api_key)
        ls_key = _decrypt_api_key(row.langsmith_api_key) if row.langsmith_api_key else ""
        return LlmConfigResponse(
            base_url=row.base_url,
            api_key=_mask_api_key(real_key),
            ranking_model=row.ranking_model,
            enrichment_model=row.enrichment_model,
            embedding_model=row.embedding_model,
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            timeout=row.timeout,
            langsmith_api_key=_mask_api_key(ls_key),
            langsmith_project=row.langsmith_project or "dental-recommend-agent",
            langsmith_enabled=bool(row.langsmith_enabled),
        )

    # Fallback to merged config (yaml + defaults)
    cfg = await llm_config_service.get_config()
    return LlmConfigResponse(
        base_url=cfg["base_url"],
        api_key=_mask_api_key(cfg["api_key"]),
        ranking_model=cfg["ranking_model"],
        enrichment_model=cfg["enrichment_model"],
        embedding_model=cfg["embedding_model"],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        timeout=cfg["timeout"],
        langsmith_api_key="",
        langsmith_project="dental-recommend-agent",
        langsmith_enabled=False,
    )


@router.post("/llm-config", response_model=LlmConfigResponse)
async def save_llm_config(
    body: LlmConfigRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save (upsert) the LLM configuration."""
    saved = await llm_config_service.save_config(body.model_dump())

    # Reconfigure LangSmith tracing via environment variables
    # LangGraph reads these automatically
    import os
    if body.langsmith_enabled and body.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = body.langsmith_api_key
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = body.langsmith_project or "dental-recommend-agent"
    else:
        os.environ.pop("LANGSMITH_TRACING", None)

    return LlmConfigResponse(
        base_url=saved["base_url"],
        api_key=_mask_api_key(saved["api_key"]),
        ranking_model=saved["ranking_model"],
        enrichment_model=saved["enrichment_model"],
        embedding_model=saved["embedding_model"],
        temperature=saved["temperature"],
        max_tokens=saved["max_tokens"],
        timeout=saved["timeout"],
        langsmith_api_key=_mask_api_key(body.langsmith_api_key) if body.langsmith_api_key else "",
        langsmith_project=body.langsmith_project,
        langsmith_enabled=body.langsmith_enabled,
    )


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(body: TestConnectionRequest):
    """Test connectivity to an OpenAI-compatible endpoint."""
    result = await llm_config_service.test_connection(body.base_url, body.api_key)
    return TestConnectionResponse(**result)


@router.get("/models", response_model=ModelsResponse)
async def get_available_models(db: AsyncSession = Depends(get_db)):
    """Return the cached list of available models from the DB."""
    result = await db.execute(select(LlmConfig).where(LlmConfig.id == 1))
    row = result.scalar_one_or_none()

    if row is not None:
        return ModelsResponse(
            models=row.available_models or [],
            updated_at=row.models_updated_at,
        )

    return ModelsResponse(models=[], updated_at=None)
