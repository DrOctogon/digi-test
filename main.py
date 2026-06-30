#!/usr/bin/env python3
"""Daily Excelta -> DigiKey scraper entrypoint.

Examples:
  python main.py --probe                 # verify creds, resolve Excelta id, sample 1 category
  python main.py                         # default sweep -> data/excelta_<today>.csv
  python main.py --mode by_category      # concurrent per-category fetch
  python main.py --date 2026-06-30       # override the date stamp
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import config
import digikey_client as dk
import pipeline
import writer


def _today() -> str:
    return dt.date.today().isoformat()


def cmd_probe() -> int:
    config.require_credentials()
    print("Requesting OAuth token...")
    dk.get_token(force=True)
    print("  OK token acquired.")

    cats = config.load_categories()
    print(f"Parsed {len(cats)} categories from link.json (first 5): "
          f"{[(c.id, c.name) for c in cats[:5]]}")

    print(f"Resolving manufacturer id for '{config.MANUFACTURER_NAME}'...")
    mid = pipeline.resolve_manufacturer_id()
    print(f"  Excelta manufacturer id = {mid}")

    sample_cat = cats[0]
    print(f"Sampling category {sample_cat.id} ({sample_cat.name})...")
    page = dk.keyword_search(manufacturer_id=mid, category_id=int(sample_cat.id), limit=3)
    count = page.get("ProductsCount", 0)
    print(f"  ProductsCount={count}; showing up to 3:")
    for p in (page.get("Products") or [])[:3]:
        mfr = (p.get("Manufacturer") or {}).get("Name", "?")
        print(f"    - {p.get('ManufacturerProductNumber','?')} | {mfr} | "
              f"{(p.get('Description') or {}).get('ProductDescription','')[:60]}")
    print("\nProbe OK. Tip: set EXCELTA_MANUFACTURER_ID in .env to skip resolution next time.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config.require_credentials()
    date = args.date or _today()
    print(f"Scrape date: {date} | categories from link.json (concurrent, auto-subdivided)")
    rows = pipeline.run(date)
    if not rows:
        print("No rows produced — check manufacturer id / filters.", file=sys.stderr)
    csv_path = writer.write_csv(rows, date)
    xlsx_path = writer.write_xlsx(rows, date)
    print(f"Wrote {len(rows)} rows -> {csv_path}")
    print(f"Wrote {len(rows)} rows -> {xlsx_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily Excelta DigiKey scraper")
    ap.add_argument("--probe", action="store_true",
                    help="Verify creds + resolve manufacturer + sample one category")
    ap.add_argument("--date", default=None, help="Date stamp YYYY-MM-DD (default: today)")
    args = ap.parse_args(argv)

    try:
        if args.probe:
            return cmd_probe()
        return cmd_run(args)
    except dk.DigiKeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
