"""
LLM configuration service.

Priority: SQLite llm_config table -> config.yaml -> hardcoded defaults.
API Key is AES-encrypted at rest.
"""

import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

import httpx
import yaml
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import async_session
from db.models import LlmConfig

# ---------------------------------------------------------------------------
# AES encryption helpers — per-installation key
# ---------------------------------------------------------------------------
_ENCRYPTION_KEY_ENV = "DENTAL_AGENT_KEY"

_KEY_FILE = os.path.join(
    os.environ.get("DENTAL_AGENT_DATA_DIR", os.path.dirname(os.path.dirname(__file__))),
    "data", ".encryption_key",
)


def _get_encryption_key() -> bytes:
    """Derive a 32-byte key. Priority:
    1. DENTAL_AGENT_KEY env var (explicit override)
    2. Per-installation key file (auto-generated on first use)
    """
    env_secret = os.environ.get(_ENCRYPTION_KEY_ENV)
    if env_secret:
        return hashlib.sha256(env_secret.encode()).digest()

    # Per-installation key: generate once, persist to file
    os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "r") as f:
            return bytes.fromhex(f.read().strip())

    # Generate random key
    key = os.urandom(32)
    with open(_KEY_FILE, "w") as f:
        f.write(key.hex())
    return key


def _encrypt_api_key(plain: str) -> str:
    """Encrypt API key using AES-CBC with base64 output."""
    if not plain:
        return ""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_padding
    key = _get_encryption_key()
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plain.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ct).decode()


def _decrypt_api_key(encrypted: str) -> str:
    """Decrypt API key."""
    if not encrypted:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding as sym_padding
        key = _get_encryption_key()
        raw = base64.b64decode(encrypted)
        iv, ct = raw[:16], raw[16:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ct) + decryptor.finalize()
        unpadder = sym_padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode()
    except Exception:
        # Fallback: return as-is (migration from unencrypted)
        return encrypted


_MASKED_PATTERN = "..."

# ---------------------------------------------------------------------------
# Hardcoded defaults (lowest priority)
# ---------------------------------------------------------------------------
_DEFAULTS: dict = {
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "ranking_model": "gpt-4o",
    "enrichment_model": "gpt-4o-mini",
    "embedding_model": "text-embedding-3-small",
    "temperature": 0.7,
    "max_tokens": 4096,
    "timeout": 30,
    "langsmith_api_key": "",
    "langsmith_project": "dental-recommend-agent",
    "langsmith_enabled": 0,
}

# ---------------------------------------------------------------------------
# config.yaml fallback (loaded once at import time)
# ---------------------------------------------------------------------------
_CONFIG_YAML_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")

def _load_yaml_defaults() -> dict:
    """Read the ``llm`` section from config.yaml, returning {} on any failure."""
    try:
        with open(_CONFIG_YAML_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("llm", {})
    except Exception:
        return {}

_YAML_DEFAULTS: dict = _load_yaml_defaults()

# ---------------------------------------------------------------------------
# Singleton OpenAI client (lazily created)
# ---------------------------------------------------------------------------
_async_client: Optional[AsyncOpenAI] = None
_client_base_url: Optional[str] = None
_client_api_key: Optional[str] = None


def _build_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """Return a fresh AsyncOpenAI client for the given credentials."""
    return AsyncOpenAI(base_url=base_url, api_key=api_key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_config() -> dict:
    """Return the merged LLM configuration dict.

    Resolution order: DB row (id=1) -> config.yaml -> hardcoded defaults.
    """
    merged: dict = {**_DEFAULTS, **_YAML_DEFAULTS}

    try:
        async with async_session() as session:
            result = await session.execute(select(LlmConfig).where(LlmConfig.id == 1))
            row: Optional[LlmConfig] = result.scalar_one_or_none()
    except Exception:
        row = None

    if row is not None:
        for key in _DEFAULTS:
            db_val = getattr(row, key, None)
            if db_val is not None:
                merged[key] = db_val
        # Decrypt api_key
        merged["api_key"] = _decrypt_api_key(merged.get("api_key", ""))
        merged["langsmith_api_key"] = _decrypt_api_key(merged.get("langsmith_api_key", ""))

    return merged


async def save_config(data: dict) -> dict:
    """Upsert LLM configuration into the ``llm_config`` table (single row, id=1).

    Returns the saved configuration as a dict (api_key decrypted).
    """
    allowed_keys = set(_DEFAULTS.keys())

    async with async_session() as session:
        result = await session.execute(select(LlmConfig).where(LlmConfig.id == 1))
        row: Optional[LlmConfig] = result.scalar_one_or_none()

        if row is None:
            row = LlmConfig(id=1)
            session.add(row)

        for key in allowed_keys:
            if key in data:
                val = data[key]
                # Skip masked API keys (e.g. "sk-xxxx...abcd")
                if key == "api_key" and isinstance(val, str) and _MASKED_PATTERN in val:
                    continue
                if key == "langsmith_api_key" and isinstance(val, str) and _MASKED_PATTERN in val:
                    continue
                # Encrypt API keys before storing
                if key == "api_key" and isinstance(val, str):
                    val = _encrypt_api_key(val)
                if key == "langsmith_api_key" and isinstance(val, str):
                    val = _encrypt_api_key(val)
                # Convert bool to int for storage
                if key == "langsmith_enabled":
                    val = 1 if val else 0
                setattr(row, key, val)

        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)

        # Return decrypted
        result_dict = {}
        for key in allowed_keys:
            val = getattr(row, key)
            if key in ("api_key", "langsmith_api_key"):
                val = _decrypt_api_key(val)
            result_dict[key] = val
        return result_dict


async def test_connection(base_url: str, api_key: str) -> dict:
    """Call ``GET {base_url}/models`` with the supplied API key.

    On success, persists the model list and connection status to the DB.

    Returns::

        {"success": True,  "models": [...], "error": None}
        {"success": False, "models": [],    "error": "..."}
    """
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            # Persist failure status
            await _persist_connection_status("error", [])
            return {
                "success": False,
                "models": [],
                "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
            }

        body = resp.json()
        model_ids: list[str] = []

        # OpenAI-compatible responses always wrap in {"data": [...]}
        raw = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and "id" in item:
                    model_ids.append(item["id"])
                elif isinstance(item, str):
                    model_ids.append(item)

        sorted_models = sorted(model_ids)

        # Persist success status + model list
        await _persist_connection_status("connected", sorted_models)

        return {"success": True, "models": sorted_models, "error": None}

    except httpx.TimeoutException:
        await _persist_connection_status("error", [])
        return {"success": False, "models": [], "error": "Connection timed out"}
    except httpx.ConnectError as exc:
        await _persist_connection_status("error", [])
        return {"success": False, "models": [], "error": f"Connection failed: {exc}"}
    except Exception as exc:
        await _persist_connection_status("error", [])
        return {"success": False, "models": [], "error": str(exc)}


async def _persist_connection_status(status: str, models: list[str]) -> None:
    """Write connection status and available models to the llm_config row."""
    try:
        async with async_session() as session:
            result = await session.execute(select(LlmConfig).where(LlmConfig.id == 1))
            row: Optional[LlmConfig] = result.scalar_one_or_none()
            if row is None:
                row = LlmConfig(id=1)
                session.add(row)
            row.connection_status = status
            row.available_models = models
            row.models_updated_at = datetime.now(timezone.utc)
            row.last_test_at = datetime.now(timezone.utc)
            await session.commit()
    except Exception:
        logger.warning("Failed to persist connection status", exc_info=True)


async def get_client() -> AsyncOpenAI:
    """Return an ``openai.AsyncOpenAI`` client configured with the current credentials.

    The client is cached and recreated only when credentials change.
    """
    global _async_client, _client_base_url, _client_api_key

    cfg = await get_config()
    base_url = cfg["base_url"]
    api_key = cfg["api_key"]

    if _async_client is None or _client_base_url != base_url or _client_api_key != api_key:
        _async_client = _build_client(base_url, api_key)
        _client_base_url = base_url
        _client_api_key = api_key

    return _async_client


async def get_model(purpose: str) -> str:
    """Return the model name configured for *purpose*.

    *purpose* must be one of ``"ranking"``, ``"enrichment"``, or ``"embedding"``.
    """
    cfg = await get_config()
    key = f"{purpose}_model"
    if key not in cfg:
        raise ValueError(f"Unknown model purpose: {purpose!r}. Expected ranking/enrichment/embedding.")
    return cfg[key]
