"""Orchestrate fetch -> parse -> dedupe across the Excelta category scope.

DigiKey's keyword search only exposes the first 300 matches of any filter
combination, so a category larger than 300 (e.g. tweezers = 556) is recursively
subdivided by a parametric filter until every slice fits the window. Categories
are fetched concurrently; results are merged deterministically with leaf (filter)
categories winning the category label over broad parent categories.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import digikey_client as dk
import parse

MAX_DEPTH = 3  # safety bound on recursive subdivision


def resolve_manufacturer_id() -> int:
    if config.MANUFACTURER_ID:
        return int(config.MANUFACTURER_ID)
    mid = dk.resolve_manufacturer_id(config.MANUFACTURER_NAME)
    if mid is None:
        raise dk.DigiKeyError(
            f"Could not resolve manufacturer id for '{config.MANUFACTURER_NAME}'. "
            f"Set EXCELTA_MANUFACTURER_ID in .env."
        )
    return mid


def _choose_filter(filters: list[dict], used_ids: set[int]) -> dict | None:
    """Pick a parametric filter to subdivide by.

    Prefer an unused filter whose largest value slice already fits the 300 window;
    among those, the one with the fewest values (fewest queries). If none fits,
    take the unused filter with the smallest max slice and recurse further.
    """
    candidates = []
    for f in filters:
        pid = f.get("ParameterId")
        values = f.get("FilterValues") or []
        if pid is None or pid in used_ids or len(values) < 2:
            continue
        counts = [v.get("ProductCount", 0) for v in values if v.get("ProductCount", 0) > 0]
        if not counts:
            continue
        candidates.append((max(counts), len(values), f))
    if not candidates:
        return None
    fits = [c for c in candidates if c[0] <= config.MAX_WINDOW]
    pool = fits or candidates
    pool.sort(key=lambda c: (c[1], c[0]))  # fewest values, then smallest max slice
    return pool[0][2]


def _collect(mid: int, cat_id: int, applied: list[dict], cat_meta: config.Category,
             date: str, out: list[dict], depth: int) -> None:
    count = dk.product_count(manufacturer_id=mid, category_id=cat_id, param_filters=applied)
    if count == 0:
        return

    if count <= config.MAX_WINDOW or depth >= MAX_DEPTH:
        for p in dk.iter_products(manufacturer_id=mid, category_id=cat_id, param_filters=applied):
            out.extend(parse.product_to_rows(p, date, category=(cat_meta.id, cat_meta.name)))
        if count > config.MAX_WINDOW:
            print(f"  ! cat {cat_meta.id} slice still {count} > {config.MAX_WINDOW} at max "
                  f"depth; capped at first {config.MAX_WINDOW}.")
        return

    filters = dk.parametric_filters(manufacturer_id=mid, category_id=cat_id, param_filters=applied)
    used = {pf.get("ParameterId") for pf in applied if pf.get("ParameterId") is not None}
    chosen = _choose_filter(filters, used)
    if chosen is None:
        for p in dk.iter_products(manufacturer_id=mid, category_id=cat_id, param_filters=applied):
            out.extend(parse.product_to_rows(p, date, category=(cat_meta.id, cat_meta.name)))
        print(f"  ! cat {cat_meta.id}: no parametric filter to subdivide {count}; capped at 300.")
        return

    pid = chosen["ParameterId"]
    for v in chosen.get("FilterValues") or []:
        if v.get("ProductCount", 0) <= 0:
            continue
        sub = applied + [{"ParameterId": pid, "FilterValues": [{"Id": v["ValueId"]}]}]
        _collect(mid, cat_id, sub, cat_meta, date, out, depth + 1)


def collect_category(mid: int, cat: config.Category, date: str) -> list[dict]:
    rows: list[dict] = []
    _collect(mid, int(cat.id), [], cat, date, rows, depth=0)
    return rows


def _merge(per_category: list[tuple[config.Category, list[dict]]]) -> list[dict]:
    """Deterministic global dedupe by DigiKey part number.

    Leaf (filter) categories are processed before parents so the more specific
    category label wins for parts that appear in both.
    """
    ordered = sorted(per_category, key=lambda t: (0 if t[0].kind == "filter" else 1, int(t[0].id)))
    seen: set[str] = set()
    out: list[dict] = []
    for _cat, rows in ordered:
        for r in rows:
            key = r.get("digikey_part_number") or f"mfr::{r.get('mfr_part_number')}"
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


def dedupe(rows: list[dict]) -> list[dict]:
    """Flat dedupe by DigiKey part number (used by tests / single-list callers)."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        key = r.get("digikey_part_number") or f"mfr::{r.get('mfr_part_number')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def run(scrape_date: str, *, max_workers: int = 8, manufacturer_id: int | None = None) -> list[dict]:
    mid = manufacturer_id if manufacturer_id is not None else resolve_manufacturer_id()
    categories = config.load_categories()
    results: list[tuple[config.Category, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(collect_category, mid, c, scrape_date): c for c in categories}
        for fut in as_completed(futures):
            cat = futures[fut]
            try:
                rows = fut.result()
                results.append((cat, rows))
                print(f"  · {cat.id} {cat.name}: {len(rows)} rows")
            except Exception as e:  # noqa: BLE001 - isolate per-category failures
                print(f"  ! category {cat.id} ({cat.name}) failed: {e}")
                results.append((cat, []))
    return _merge(results)
