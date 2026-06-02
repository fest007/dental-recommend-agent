"""
Product enrichment service.

Uses LLM to extract structured metadata (brand, category, product type,
consumables, etc.) from raw product names.
"""

import json
import logging
import re
import time
from typing import Any, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template (Chinese)
# ---------------------------------------------------------------------------

_ENRICHMENT_PROMPT = """你是一个牙科设备行业专家。请根据以下商品名称，推断并输出结构化信息。

商品名称：{product_name}
SKU编码：{sku}

请以JSON格式输出以下字段（如无法确定则填null）：

{{
  "brand": "品牌名称（从名称中识别，如3M、GC、Dentsply等，无法识别则填null）",
  "category_l1": "一级品类（修复材料/正畸器材/影像设备/种植系统/消毒灭菌/口腔外科/根管治疗/牙周治疗/预防保健/技工耗材/设备器械/其他）",
  "category_l2": "二级品类（如：树脂水门汀、藻酸盐印模材、CBCT、光固化灯等）",
  "product_type": "产品类型（equipment/consumable/tool/accessory/reagent/material）",
  "usage_scenario": "使用场景简述（30字以内）",
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "consumables": [
    {{"name": "可能的消耗品名称", "relation": "配套消耗/易损件"}}
  ],
  "related_accessories": [
    {{"name": "可能的配件名称", "relation": "适配配件"}}
  ],
  "typical_purchase_cycle_days": null,
  "unit_hint": "最小使用单位（如：个、支、盒、瓶、包、台）"
}}

注意事项：
1. 请只输出JSON，不要输出其他内容。
2. brand如果无法识别请填null，不要编造品牌名。
3. category_l1必须从给定的选项中选择。
4. product_type必须从给定的英文选项中选择。
5. keywords至少输出2个，最多5个。
6. consumables和related_accessories如果无法确定可以输出空数组[]。
7. typical_purchase_cycle_days如果是耗材类商品请估算天数，设备类填null。
"""

# ---------------------------------------------------------------------------
# Name cleaning
# ---------------------------------------------------------------------------

def _clean_product_name(name: str) -> str:
    """Remove packaging/grade prefixes from product names."""
    # Remove bracketed prefixes: 【盒】【瓶】【包】【支】【台】【卡】【过期】 etc.
    name = re.sub(r"[【\[][^】\]]*[】\]]", "", name).strip()
    # Remove grade prefixes at the start
    name = re.sub(r"^[OA]级\s*", "", name).strip()
    return name


def _parse_llm_json(raw: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from LLM output."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to find the first { ... } block
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# Defaults for parsed fields
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "brand": "",
    "category_l1": "",
    "category_l2": "",
    "product_type": "consumable",
    "usage_scenario": "",
    "keywords": [],
    "consumables": [],
    "related_accessories": [],
    "typical_purchase_cycle_days": None,
    "unit_hint": "个",
}

# Post-processing normalization map (§3.2.5)
# Maps common LLM output variations to the canonical category_l1 values.
CATEGORY_NORMALIZE: dict[str, str] = {
    "树脂水门汀": "修复材料",
    "玻璃离子": "修复材料",
    "光固化树脂": "修复材料",
    "印模材": "修复材料",
    "印模材料": "修复材料",
    "种植体": "种植系统",
    "骨粉": "种植系统",
    "骨膜": "种植系统",
    "基台": "种植系统",
    "根管锉": "根管治疗",
    "牙胶尖": "根管治疗",
    "根管封闭剂": "根管治疗",
    "托槽": "正畸器材",
    "弓丝": "正畸器材",
    "橡皮链": "正畸器材",
    "洁治器": "牙周治疗",
    "刮治器": "牙周治疗",
    "超声洁牙机": "设备器械",
    "光固化灯": "设备器械",
    "CBCT": "影像设备",
    "口内扫描仪": "影像设备",
}


def _apply_defaults(parsed: dict) -> dict:
    """Fill missing keys with sensible defaults and normalize categories."""
    result = dict(_DEFAULTS)
    for key in result:
        if key in parsed and parsed[key] is not None:
            result[key] = parsed[key]
    # Ensure lists are lists
    for key in ("keywords", "consumables", "related_accessories"):
        if not isinstance(result[key], list):
            result[key] = []

    # Normalize category_l1 using the mapping table
    cat = result.get("category_l1", "")
    if cat in CATEGORY_NORMALIZE:
        result["category_l1"] = CATEGORY_NORMALIZE[cat]

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_product(
    product_name: str,
    sku: str,
    llm_service: OpenAI,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 30,
) -> dict:
    """Call LLM to extract structured information from a product name.

    Parameters
    ----------
    product_name : str
        Raw product name (will be cleaned before sending to LLM).
    sku : str
        SKU code.
    llm_service : OpenAI
        A synchronous ``OpenAI`` client instance (configured via
        ``LLMConfigService`` or constructed directly).
    model : str
        Model name for the enrichment call.
    temperature : float
        Sampling temperature.
    max_tokens : int
        Maximum output tokens.
    timeout : int
        Request timeout in seconds.

    Returns
    -------
    dict
        Enriched product dict with keys matching the ``products_enriched``
        schema.  On failure the dict will contain an ``"error"`` key.
    """
    cleaned_name = _clean_product_name(product_name)
    prompt = _ENRICHMENT_PROMPT.format(product_name=cleaned_name, sku=sku)

    try:
        response = llm_service.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个牙科设备行业专家，擅长从商品名称中提取结构化信息。请严格按照要求的JSON格式输出。",
                },
                {"role": "user", "content": prompt},
            ],
        )

        raw_text = response.choices[0].message.content or ""
        parsed = _parse_llm_json(raw_text)

        if parsed is None:
            logger.warning("Failed to parse LLM JSON for SKU=%s: %s", sku, raw_text[:200])
            return {
                **_apply_defaults({}),
                "sku": sku,
                "name": product_name,
                "error": "json_parse_failed",
                "raw_output": raw_text[:500],
            }

        result = _apply_defaults(parsed)
        result["sku"] = sku
        result["name"] = product_name
        result["llm_model"] = model
        result["enrichment_confidence"] = 0.85  # default; could be refined
        return result

    except Exception as exc:
        logger.error("enrich_product failed for SKU=%s: %s", sku, exc, exc_info=True)
        return {
            **_apply_defaults({}),
            "sku": sku,
            "name": product_name,
            "error": str(exc),
        }


def batch_enrich(
    products: list[dict],
    llm_service: OpenAI,
    batch_size: int = 20,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 30,
) -> list[dict]:
    """Enrich a list of products in batches with retry logic.

    Parameters
    ----------
    products : list[dict]
        Each dict must have ``"product_name"`` and ``"sku"`` keys.
    llm_service : OpenAI
        Synchronous OpenAI client.
    batch_size : int
        Number of products per batch (used for logging/progress; each
        product is still enriched individually).
    model : str
        Model name for enrichment.
    temperature : float
        Sampling temperature.
    max_tokens : int
        Maximum output tokens.
    timeout : int
        Request timeout in seconds.

    Returns
    -------
    list[dict]
        List of enriched product dicts in the same order as *products*.
    """
    max_retries = 2
    results: list[dict] = []
    total = len(products)

    for i in range(0, total, batch_size):
        batch = products[i : i + batch_size]
        logger.info(
            "Enriching batch %d-%d / %d",
            i + 1,
            min(i + batch_size, total),
            total,
        )

        for item in batch:
            product_name = item.get("product_name", "")
            sku = item.get("sku", "")

            enriched: Optional[dict] = None
            last_error: Optional[str] = None

            for attempt in range(1, max_retries + 1):
                result = enrich_product(
                    product_name, sku, llm_service,
                    model=model, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout,
                )
                if "error" not in result:
                    enriched = result
                    break
                last_error = result.get("error", "unknown")
                logger.warning(
                    "Attempt %d/%d failed for SKU=%s: %s",
                    attempt,
                    max_retries,
                    sku,
                    last_error,
                )
                if attempt < max_retries:
                    # Simple linear back-off: 2s, 4s
                    time.sleep(2 * attempt)

            if enriched is None:
                # All retries exhausted – record the failure
                logger.error(
                    "All %d retries exhausted for SKU=%s", max_retries, sku
                )
                enriched = {
                    **_apply_defaults({}),
                    "sku": sku,
                    "name": product_name,
                    "error": f"retries_exhausted: {last_error}",
                }

            results.append(enriched)

    return results
