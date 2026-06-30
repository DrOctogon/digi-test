"""Normalize DigiKey v4 product objects into flat CSV rows.

One row per ProductVariation (a DigiKey part number) — that mirrors the rows a
DigiKey storefront table shows. Products with no variations yield a single
product-level row. Long-tail attributes are preserved as JSON columns so the CSV
schema stays stable across heterogeneous categories.
"""
from __future__ import annotations

import json
from typing import Any

# Stable column order for the CSV.
ROW_COLUMNS = [
    "scrape_date",
    "manufacturer",
    "mfr_part_number",
    "digikey_part_number",
    "description",
    "detailed_description",
    "category_id",
    "category_name",
    "unit_price",
    "quantity_available",
    "min_order_qty",
    "package_type",
    "datasheet_url",
    "product_url",
    "photo_url",
    "price_breaks_json",
    "parameters_json",
]


def _g(d: Any, *keys, default=None):
    """Safe nested getter: _g(obj, 'a', 'b') == obj['a']['b'] or default."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def flatten_category_ids(product: dict) -> list[str]:
    """Return every CategoryId in the product's category hierarchy (as strings)."""
    ids: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            cid = node.get("CategoryId")
            if cid is not None:
                ids.append(str(cid))
            for child in node.get("ChildCategories") or []:
                walk(child)
        elif isinstance(node, list):
            for n in node:
                walk(n)

    walk(product.get("Category"))
    return ids


def leaf_category(product: dict) -> tuple[str, str]:
    """Deepest (id, name) in the category hierarchy."""
    cid, name = "", ""

    def walk(node: Any) -> None:
        nonlocal cid, name
        if isinstance(node, dict):
            if node.get("CategoryId") is not None:
                cid, name = str(node["CategoryId"]), str(node.get("Name", ""))
            for child in node.get("ChildCategories") or []:
                walk(child)

    walk(product.get("Category"))
    return cid, name


def _lowest_unit_price(variation: dict, product: dict) -> Any:
    pricing = variation.get("StandardPricing") or []
    prices = [p.get("UnitPrice") for p in pricing if p.get("UnitPrice") is not None]
    if prices:
        return min(prices)
    return product.get("UnitPrice")


def product_to_rows(product: dict, scrape_date: str,
                    category: tuple[str, str] | None = None) -> list[dict]:
    # Prefer the queried (link) category when provided — the API's Category field
    # collapses to the top-level node, which is too coarse for per-link output.
    if category is not None:
        cat_id, cat_name = category
    else:
        cat_id, cat_name = leaf_category(product)
    base = {
        "scrape_date": scrape_date,
        "manufacturer": _g(product, "Manufacturer", "Name", default=""),
        "mfr_part_number": product.get("ManufacturerProductNumber", ""),
        "description": _g(product, "Description", "ProductDescription", default=""),
        "detailed_description": _g(product, "Description", "DetailedDescription", default=""),
        "category_id": cat_id,
        "category_name": cat_name,
        "datasheet_url": product.get("DatasheetUrl", ""),
        "product_url": product.get("ProductUrl", ""),
        "photo_url": product.get("PhotoUrl", ""),
        "parameters_json": json.dumps([
            {"name": p.get("ParameterText", ""), "value": p.get("ValueText", "")}
            for p in (product.get("Parameters") or [])
        ], ensure_ascii=False),
    }

    variations = product.get("ProductVariations") or []
    if not variations:
        row = dict(base)
        row.update({
            "digikey_part_number": "",
            "unit_price": product.get("UnitPrice", ""),
            "quantity_available": product.get("QuantityAvailable", ""),
            "min_order_qty": "",
            "package_type": "",
            "price_breaks_json": "[]",
        })
        return [_ordered(row)]

    rows = []
    for v in variations:
        row = dict(base)
        row.update({
            "digikey_part_number": v.get("DigiKeyProductNumber", ""),
            "unit_price": _lowest_unit_price(v, product),
            "quantity_available": v.get(
                "QuantityAvailableforPackageType", product.get("QuantityAvailable", "")
            ),
            "min_order_qty": v.get("MinimumOrderQuantity", ""),
            "package_type": _g(v, "PackageType", "Name", default=""),
            "price_breaks_json": json.dumps(v.get("StandardPricing") or [], ensure_ascii=False),
        })
        rows.append(_ordered(row))
    return rows


def _ordered(row: dict) -> dict:
    """Project a row onto ROW_COLUMNS (fills missing keys with '')."""
    return {col: row.get(col, "") for col in ROW_COLUMNS}
