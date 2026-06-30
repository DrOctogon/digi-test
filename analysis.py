"""Excelta @ DigiKey — baseline analysis (T=0 single snapshot).

MBB-style modules, each grounded only in what one snapshot can support:
  WS1 validate()          data integrity + quarantine flags (the "no mistakes" gate)
  WS2 abc_analysis()      Pareto / ABC by inventory value
  WS3 stock_health()      availability & stranded-value by category
  WS4 price_architecture()  intra-category price dispersion + discount ladders
  WS5 assortment()        price x stock quadrant; dead / hero / niche

Guardrails baked in:
  * $0 price excluded from price stats, surfaced separately (quote-only).
  * qty=0 is a real state, never imputed.
  * inventory_value = list_price * visible_stock = PROXY (not COGS / sell-through).
  * right-skewed price -> median-first reporting.
  * No time-series here: one snapshot = baseline only.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import statistics as st
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import config

INV_VALUE_PROXY_NOTE = "inventory_value = list_price x visible_stock (proxy; not COGS/sell-through)"


# --------------------------------------------------------------------------- #
# Loading & numeric coercion                                                  #
# --------------------------------------------------------------------------- #
def latest_csv(data_dir: Path = config.DATA_DIR) -> Path:
    files = sorted(glob.glob(str(data_dir / "excelta_*.csv")))
    files = [f for f in files if "analysis" not in os.path.basename(f)]
    if not files:
        raise SystemExit("No snapshot CSV found in data/. Run main.py first.")
    return Path(files[-1])


def num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def inv_value(row: dict) -> float:
    p, q = num(row.get("unit_price")), num(row.get("quantity_available"))
    if p is None or q is None or p <= 0 or q <= 0:
        return 0.0
    return p * q


# --------------------------------------------------------------------------- #
# WS1 — validation                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class Quality:
    total: int = 0
    issues: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def validate(rows: list[dict], scope_ids: Optional[set[str]] = None) -> Quality:
    scope_ids = scope_ids or config.category_id_set()
    q = Quality(total=len(rows))
    seen: set[str] = set()
    dupes = 0
    checks = {
        "missing_dk_part": 0, "missing_mfr_part": 0, "wrong_manufacturer": 0,
        "negative_price": 0, "negative_qty": 0, "non_numeric_price": 0,
        "non_numeric_qty": 0, "out_of_scope_category": 0,
        "zero_price_quote_only": 0, "zero_stock": 0,
        "missing_datasheet": 0, "missing_photo": 0,
    }
    for r in rows:
        dk = r.get("digikey_part_number", "")
        if not dk:
            checks["missing_dk_part"] += 1
        elif dk in seen:
            dupes += 1
        else:
            seen.add(dk)
        if not r.get("mfr_part_number"):
            checks["missing_mfr_part"] += 1
        if "excelta" not in str(r.get("manufacturer", "")).lower():
            checks["wrong_manufacturer"] += 1
        p, qy = num(r.get("unit_price")), num(r.get("quantity_available"))
        if p is None:
            checks["non_numeric_price"] += 1
        elif p < 0:
            checks["negative_price"] += 1
        elif p == 0:
            checks["zero_price_quote_only"] += 1
        if qy is None:
            checks["non_numeric_qty"] += 1
        elif qy < 0:
            checks["negative_qty"] += 1
        elif qy == 0:
            checks["zero_stock"] += 1
        if scope_ids and str(r.get("category_id")) not in scope_ids:
            checks["out_of_scope_category"] += 1
        if not r.get("datasheet_url"):
            checks["missing_datasheet"] += 1
        if not r.get("photo_url"):
            checks["missing_photo"] += 1
    checks["duplicate_dk_part"] = dupes
    q.issues = checks
    # Blocking issues = data correctness; the rest are informational states.
    blocking = ("missing_dk_part", "duplicate_dk_part", "wrong_manufacturer",
                "negative_price", "negative_qty", "non_numeric_price", "non_numeric_qty")
    q.notes.append("PASS" if all(checks[k] == 0 for k in blocking) else "FAIL")
    q.notes.append(INV_VALUE_PROXY_NOTE)
    return q


# --------------------------------------------------------------------------- #
# WS2 — ABC / Pareto                                                          #
# --------------------------------------------------------------------------- #
def abc_analysis(rows: list[dict]) -> dict:
    valued = [(r, inv_value(r)) for r in rows]
    valued = [(r, v) for r, v in valued if v > 0]
    valued.sort(key=lambda t: t[1], reverse=True)
    total = sum(v for _, v in valued)
    out_rows, cum = [], 0.0
    for r, v in valued:
        cum += v
        share = cum / total if total else 0
        tier = "A" if share <= 0.80 else ("B" if share <= 0.95 else "C")
        out_rows.append({
            "digikey_part_number": r["digikey_part_number"],
            "mfr_part_number": r["mfr_part_number"],
            "category_name": r["category_name"],
            "description": r["description"][:60],
            "unit_price": num(r["unit_price"]),
            "quantity_available": int(num(r["quantity_available"]) or 0),
            "inventory_value": round(v, 2),
            "cum_value_share": round(share, 4),
            "abc_tier": tier,
        })
    tiers = {"A": [0, 0.0], "B": [0, 0.0], "C": [0, 0.0]}
    for r in out_rows:
        t = tiers[r["abc_tier"]]
        t[0] += 1
        t[1] += r["inventory_value"]
    return {
        "rows": out_rows,
        "total_inventory_value": round(total, 2),
        "valued_sku_count": len(out_rows),
        "tier_summary": {k: {"skus": v[0], "value": round(v[1], 2),
                             "value_share": round(v[1] / total, 4) if total else 0}
                         for k, v in tiers.items()},
    }


# --------------------------------------------------------------------------- #
# WS3 — stock health                                                          #
# --------------------------------------------------------------------------- #
def stock_health(rows: list[dict]) -> dict:
    by_cat: dict[str, dict] = {}
    for r in rows:
        c = r["category_name"]
        q = num(r.get("quantity_available")) or 0
        p = num(r.get("unit_price")) or 0
        d = by_cat.setdefault(c, {"parts": 0, "in_stock": 0, "units": 0,
                                  "inv_value": 0.0, "oos_list_exposure": 0.0})
        d["parts"] += 1
        if q > 0:
            d["in_stock"] += 1
            d["units"] += q
            d["inv_value"] += p * q
        else:
            d["oos_list_exposure"] += p  # per-SKU list price stranded by stockout (proxy)
    cats = []
    for c, d in by_cat.items():
        cats.append({
            "category_name": c,
            "parts": d["parts"],
            "in_stock": d["in_stock"],
            "stockout_rate": round(1 - d["in_stock"] / d["parts"], 4) if d["parts"] else 0,
            "units_available": int(d["units"]),
            "inventory_value": round(d["inv_value"], 2),
            "oos_list_exposure": round(d["oos_list_exposure"], 2),
        })
    cats.sort(key=lambda x: x["inventory_value"], reverse=True)
    tot_parts = sum(c["parts"] for c in cats)
    tot_instock = sum(c["in_stock"] for c in cats)
    return {
        "categories": cats,
        "overall_stockout_rate": round(1 - tot_instock / tot_parts, 4) if tot_parts else 0,
        "total_units": int(sum(c["units_available"] for c in cats)),
        "total_inventory_value": round(sum(c["inventory_value"] for c in cats), 2),
    }


# --------------------------------------------------------------------------- #
# WS4 — price architecture                                                    #
# --------------------------------------------------------------------------- #
def _discount_depth(price_breaks_json: str) -> Optional[float]:
    try:
        breaks = json.loads(price_breaks_json)
    except (TypeError, ValueError):
        return None
    pts = [(num(b.get("BreakQuantity")), num(b.get("UnitPrice"))) for b in breaks]
    pts = [(bq, up) for bq, up in pts if bq and up and up > 0]
    if len(pts) < 2:
        return None
    pts.sort(key=lambda t: t[0])
    hi, lo = pts[0][1], pts[-1][1]
    return round((hi - lo) / hi, 4) if hi > 0 else None


def price_architecture(rows: list[dict]) -> dict:
    priced = [r for r in rows if (num(r.get("unit_price")) or 0) > 0]
    prices = [num(r["unit_price"]) for r in priced]
    cats: dict[str, list[float]] = {}
    for r in priced:
        cats.setdefault(r["category_name"], []).append(num(r["unit_price"]))
    cat_disp = []
    for c, ps in cats.items():
        if len(ps) < 3:
            continue
        mean = st.mean(ps)
        cv = st.pstdev(ps) / mean if mean else 0
        cat_disp.append({
            "category_name": c, "priced_skus": len(ps),
            "median_price": round(st.median(ps), 3),
            "min_price": round(min(ps), 3), "max_price": round(max(ps), 3),
            "price_spread_ratio": round(max(ps) / min(ps), 1) if min(ps) else None,
            "coef_of_variation": round(cv, 3),
        })
    cat_disp.sort(key=lambda x: x["coef_of_variation"], reverse=True)
    depths = [d for r in rows if (d := _discount_depth(r.get("price_breaks_json", ""))) is not None]
    return {
        "priced_sku_count": len(priced),
        "zero_price_count": len(rows) - len(priced),
        "price_median": round(st.median(prices), 3) if prices else None,
        "price_mean": round(st.mean(prices), 2) if prices else None,
        "price_p90": round(sorted(prices)[int(len(prices) * 0.9)], 2) if prices else None,
        "price_max": round(max(prices), 2) if prices else None,
        "category_dispersion": cat_disp,
        "skus_with_volume_discount": len(depths),
        "median_discount_depth": round(st.median(depths), 4) if depths else None,
    }


# --------------------------------------------------------------------------- #
# WS5 — assortment quadrant                                                   #
# --------------------------------------------------------------------------- #
def assortment(rows: list[dict]) -> dict:
    prices = [num(r["unit_price"]) for r in rows if (num(r.get("unit_price")) or 0) > 0]
    price_median = st.median(prices) if prices else 0
    buckets = {"hero": 0, "premium_niche": 0, "value_stock": 0,
               "dead_or_quote": 0, "long_tail": 0}
    for r in rows:
        p = num(r.get("unit_price")) or 0
        q = num(r.get("quantity_available")) or 0
        if p <= 0:
            buckets["dead_or_quote"] += 1
        elif q > 0 and p >= price_median:
            buckets["hero"] += 1
        elif q > 0 and p < price_median:
            buckets["value_stock"] += 1
        else:  # q <= 0 and p > 0
            buckets["premium_niche"] += 1
    return {
        "price_median_cut": round(price_median, 3),
        "buckets": buckets,
        "dead_or_quote_skus": buckets["dead_or_quote"],
        "rationalization_candidates": sum(1 for r in rows
                                          if (num(r.get("unit_price")) or 0) <= 0
                                          and (num(r.get("quantity_available")) or 0) <= 0),
    }


# --------------------------------------------------------------------------- #
# Competitive / footprint lens (single-brand caveat)                          #
# --------------------------------------------------------------------------- #
def footprint(rows: list[dict], stock: dict) -> dict:
    cats = stock["categories"]
    return {
        "categories_covered": len(cats),
        "total_skus": len(rows),
        "breadth_note": "single-brand snapshot — positioning is relative to Excelta's own catalog, not competitors",
        "deepest_category": cats[0]["category_name"] if cats else None,
        "deepest_category_skus": cats[0]["parts"] if cats else 0,
        "availability_competitiveness": round(1 - stock["overall_stockout_rate"], 4),
    }


def run_all(rows: list[dict]) -> dict:
    q = validate(rows)
    abc = abc_analysis(rows)
    stock = stock_health(rows)
    price = price_architecture(rows)
    assort = assortment(rows)
    fp = footprint(rows, stock)
    return {"quality": q, "abc": abc, "stock": stock,
            "price": price, "assortment": assort, "footprint": fp}
