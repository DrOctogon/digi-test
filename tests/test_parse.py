"""Offline tests for parse.py + pipeline.dedupe using a synthetic v4 product."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parse  # noqa: E402
import pipeline  # noqa: E402

# Synthetic DigiKey Product Information v4 product (shape only, fake values).
SAMPLE = {
    "Manufacturer": {"Id": 562, "Name": "Excelta"},
    "ManufacturerProductNumber": "7-SA",
    "Description": {
        "ProductDescription": "TWEEZER STRAIGHT FINE POINT 4.5\"",
        "DetailedDescription": "Tweezers Stainless Steel Fine Straight 4.50\"",
    },
    "DatasheetUrl": "https://example.com/7-SA.pdf",
    "ProductUrl": "https://www.digikey.com/en/products/detail/excelta/7-SA/000000",
    "PhotoUrl": "https://example.com/7-SA.jpg",
    "QuantityAvailable": 120,
    "UnitPrice": 25.10,
    "Category": {
        "CategoryId": 17, "Name": "Tools",
        "ChildCategories": [
            {"CategoryId": 240, "Name": "Tweezers", "ChildCategories": []}
        ],
    },
    "Parameters": [
        {"ParameterText": "Tip Shape", "ValueText": "Fine Point"},
        {"ParameterText": "Material", "ValueText": "Stainless Steel"},
    ],
    "ProductVariations": [
        {
            "DigiKeyProductNumber": "7-SA-ND",
            "PackageType": {"Name": "Bulk"},
            "QuantityAvailableforPackageType": 120,
            "MinimumOrderQuantity": 1,
            "StandardPricing": [
                {"BreakQuantity": 1, "UnitPrice": 25.10, "TotalPrice": 25.10},
                {"BreakQuantity": 10, "UnitPrice": 22.00, "TotalPrice": 220.00},
            ],
        }
    ],
}


def test_flatten_category_ids_walks_hierarchy():
    assert set(parse.flatten_category_ids(SAMPLE)) == {"17", "240"}


def test_leaf_category_is_deepest():
    assert parse.leaf_category(SAMPLE) == ("240", "Tweezers")


def test_product_to_rows_one_per_variation():
    rows = parse.product_to_rows(SAMPLE, "2026-06-30")
    assert len(rows) == 1
    r = rows[0]
    assert r["scrape_date"] == "2026-06-30"
    assert r["manufacturer"] == "Excelta"
    assert r["mfr_part_number"] == "7-SA"
    assert r["digikey_part_number"] == "7-SA-ND"
    assert r["category_id"] == "240"
    assert r["package_type"] == "Bulk"
    # Lowest price break wins.
    assert r["unit_price"] == 22.00
    # Every column present and in stable order.
    assert list(r.keys()) == parse.ROW_COLUMNS


def test_product_without_variations_yields_one_row():
    prod = dict(SAMPLE)
    prod["ProductVariations"] = []
    rows = parse.product_to_rows(prod, "2026-06-30")
    assert len(rows) == 1
    assert rows[0]["digikey_part_number"] == ""
    assert rows[0]["unit_price"] == 25.10


def test_dedupe_by_digikey_part():
    rows = parse.product_to_rows(SAMPLE, "2026-06-30") * 3
    assert len(pipeline.dedupe(rows)) == 1
