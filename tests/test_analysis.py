"""Offline tests for analysis.py with a tiny synthetic snapshot."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis as A  # noqa: E402


def _row(dk, mfr, cat_id, cat, price, qty, breaks=None):
    return {
        "scrape_date": "2026-06-30", "manufacturer": "Excelta Corporation",
        "mfr_part_number": mfr, "digikey_part_number": dk,
        "description": f"PART {mfr}", "detailed_description": "",
        "category_id": cat_id, "category_name": cat,
        "unit_price": str(price), "quantity_available": str(qty),
        "min_order_qty": "1", "package_type": "Bulk",
        "datasheet_url": "x", "product_url": "x", "photo_url": "x",
        "price_breaks_json": json.dumps(breaks or []), "parameters_json": "[]",
    }


# 240 is a real scope id; mix of stocked, out-of-stock, and $0 quote-only.
ROWS = [
    _row("A-ND", "A", "240", "tweezers", 100.0, 10,
         [{"BreakQuantity": 1, "UnitPrice": 100.0}, {"BreakQuantity": 10, "UnitPrice": 80.0}]),
    _row("B-ND", "B", "240", "tweezers", 50.0, 4),
    _row("C-ND", "C", "243", "pliers", 10.0, 0),       # out of stock
    _row("D-ND", "D", "243", "pliers", 0.0, 0),        # quote-only + dead
]


def test_inv_value_proxy_excludes_zero():
    assert A.inv_value(ROWS[0]) == 1000.0   # 100 * 10
    assert A.inv_value(ROWS[2]) == 0.0      # qty 0
    assert A.inv_value(ROWS[3]) == 0.0      # price 0


def test_validate_passes_and_counts_states():
    q = A.validate(ROWS, scope_ids={"240", "243"})
    assert q.notes[0] == "PASS"             # no blocking issues
    assert q.issues["zero_stock"] == 2
    assert q.issues["zero_price_quote_only"] == 1
    assert q.issues["duplicate_dk_part"] == 0
    assert q.issues["out_of_scope_category"] == 0


def test_abc_value_share_sums_to_one():
    abc = A.abc_analysis(ROWS)
    assert abc["valued_sku_count"] == 2     # only A and B have price>0 & stock>0
    assert abc["total_inventory_value"] == 1200.0
    shares = sum(t["value_share"] for t in abc["tier_summary"].values())
    assert abs(shares - 1.0) < 1e-9


def test_stock_health_rates():
    stock = A.stock_health(ROWS)
    # 2 of 4 in stock overall
    assert stock["overall_stockout_rate"] == 0.5
    pliers = next(c for c in stock["categories"] if c["category_name"] == "pliers")
    assert pliers["stockout_rate"] == 1.0   # both pliers out of stock


def test_discount_depth():
    depth = A._discount_depth(ROWS[0]["price_breaks_json"])
    assert depth == 0.2                     # (100-80)/100
    assert A._discount_depth(ROWS[1]["price_breaks_json"]) is None


def test_assortment_buckets_cover_all():
    a = A.assortment(ROWS)
    assert sum(a["buckets"].values()) == len(ROWS)
    assert a["rationalization_candidates"] == 1   # the $0 + 0-stock SKU
