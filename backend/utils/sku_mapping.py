"""SKU mapping helpers – build and apply old-SKU -> new-SKU mappings."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_sku_mapping(products: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, str]]]:
    """
    Build an ``old_sku -> new_sku`` mapping dict from a list of product dicts.

    Each product dict is expected to have at least the keys ``"sku"`` and
    ``"old_sku"`` (as produced by :func:`excel_parser.parse_products_excel`).

    Only entries where ``old_sku`` is a non-empty string are included.

    Returns:
        A tuple of (mapping_dict, conflicts_list).
        mapping_dict: old_sku -> new_sku (first-seen wins on conflict)
        conflicts_list: list of {"old_sku", "new_sku", "existing_sku"} for
            cases where the same old_sku maps to multiple new_skus.
    """
    mapping: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []

    for product in products:
        old_sku = (product.get("old_sku") or "").strip().upper()
        new_sku = (product.get("sku") or "").strip().upper()
        if old_sku and new_sku:
            if old_sku in mapping:
                existing = mapping[old_sku]
                if existing != new_sku:
                    conflicts.append({
                        "old_sku": old_sku,
                        "new_sku": new_sku,
                        "existing_sku": existing,
                    })
                    logger.warning(
                        "SKU mapping conflict: old_sku=%s maps to both %s and %s (keeping first)",
                        old_sku, existing, new_sku,
                    )
            else:
                mapping[old_sku] = new_sku

    return mapping, conflicts


def standardize_purchase(
    purchase: dict[str, Any], mapping: dict[str, str]
) -> dict[str, Any]:
    """
    Return a **copy** of *purchase* with its ``"sku"`` replaced by the
    corresponding new SKU if one exists in *mapping*.

    The original SKU value is preserved in the ``"original_sku"`` field.

    Args:
        purchase: A single purchase dict (as produced by
            :func:`excel_parser.parse_purchases_excel`).
        mapping: The ``old_sku -> new_sku`` dict returned by
            :func:`build_sku_mapping`.

    Returns:
        A new dict with potentially updated ``"sku"`` and ``"original_sku"``.
    """
    result = dict(purchase)
    current_sku = (result.get("sku") or "").strip().upper()

    # Normalize mapping keys AND values to uppercase for consistent matching
    upper_mapping = {k.strip().upper(): v.strip().upper() for k, v in mapping.items()}

    if current_sku in upper_mapping:
        result["original_sku"] = current_sku
        result["sku"] = upper_mapping[current_sku]
    else:
        # Keep whatever original_sku was already set (may be "").
        if not result.get("original_sku"):
            result["original_sku"] = current_sku

    return result
