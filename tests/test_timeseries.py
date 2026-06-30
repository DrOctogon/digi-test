"""Offline tests for timeseries.py (WS6) using two synthetic snapshots."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import timeseries as T  # noqa: E402


def idx(*parts):
    """parts: (dk, price, qty) -> {dk: row-like}."""
    return {dk: {"digikey_part_number": dk, "unit_price": str(p),
                 "quantity_available": str(q), "category_name": "tweezers"}
            for dk, p, q in parts}


def test_restock_and_stockout_events():
    prev = idx(("A", 10, 0), ("B", 10, 5))       # A oos, B in stock
    cur = idx(("A", 10, 7), ("B", 10, 0))        # A restocked, B stocked out
    d = T.diff_pair(prev, cur)
    assert d["restock_events"] == 1
    assert d["stockout_events"] == 1


def test_new_and_discontinued():
    prev = idx(("A", 10, 1))
    cur = idx(("A", 10, 1), ("NEW", 5, 2))       # NEW added, none dropped
    d = T.diff_pair(prev, cur)
    assert d["new_skus"] == ["NEW"]
    assert d["discontinued_skus"] == []
    prev2 = idx(("A", 10, 1), ("OLD", 5, 2))
    cur2 = idx(("A", 10, 1))
    assert T.diff_pair(prev2, cur2)["discontinued_skus"] == ["OLD"]


def test_price_move_and_demand_proxy():
    prev = idx(("A", 10.0, 20))
    cur = idx(("A", 12.5, 12))                    # +2.50 price, 8 units removed
    d = T.diff_pair(prev, cur)
    assert d["price_moves"] == 1
    assert d["demand_units_proxy"] == 8
    chg = d["changes"][0]
    assert chg["price_delta"] == 2.5
    assert chg["qty_delta"] == -8


def test_zero_price_not_counted_as_price_move():
    prev = idx(("A", 0.0, 5))
    cur = idx(("A", 0.0, 5))
    assert T.diff_pair(prev, cur)["price_moves"] == 0
