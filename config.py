"""Central config: env, paths, and the category scope parsed from link.json.

The 31 URLs in link.json are DigiKey category/filter pages, all pre-filtered to
the Excelta manufacturer (the shared `s=` param). Each URL's trailing path segment
is a DigiKey CategoryId. We parse those ids here so the scraper's scope exactly
mirrors the supplied links.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

LINKS_FILE = ROOT / "link.json"
DATA_DIR = ROOT / "data"
TOKEN_CACHE = ROOT / ".token_cache.json"

# --- DigiKey API config (from .env) ---
CLIENT_ID = os.environ.get("DIGIKEY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DIGIKEY_CLIENT_SECRET", "")
API_BASE = os.environ.get("DIGIKEY_API_BASE", "https://api.digikey.com").rstrip("/")
LOCALE_SITE = os.environ.get("DIGIKEY_LOCALE_SITE", "US")
LOCALE_LANGUAGE = os.environ.get("DIGIKEY_LOCALE_LANGUAGE", "en")
LOCALE_CURRENCY = os.environ.get("DIGIKEY_LOCALE_CURRENCY", "USD")

MANUFACTURER_NAME = os.environ.get("EXCELTA_MANUFACTURER_NAME", "Excelta")
# Optional pre-resolved id; blank => resolved at runtime from the name.
MANUFACTURER_ID = os.environ.get("EXCELTA_MANUFACTURER_ID", "").strip()

TOKEN_URL = f"{API_BASE}/v1/oauth2/token"
KEYWORD_SEARCH_URL = f"{API_BASE}/products/v4/search/keyword"

# DigiKey keyword-search caps: max rows per page, and max reachable window
# (Offset + Limit must be <= 300).
PAGE_LIMIT = 50
MAX_WINDOW = 300


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    kind: str  # "category" (parent) or "filter" (leaf)
    url: str


_SEG_RE = re.compile(r"/products/(category|filter)/(.+?)/(\d+)\?", re.IGNORECASE)


def load_categories(links_file: Path = LINKS_FILE) -> list[Category]:
    """Parse link.json -> ordered, de-duplicated list of DigiKey categories.

    The id is the trailing numeric path segment; the name is the segment before it.
    """
    raw = json.loads(links_file.read_text())
    out: dict[str, Category] = {}
    for url in raw.get("links", []):
        m = _SEG_RE.search(url)
        if not m:
            continue
        kind, path, cid = m.group(1).lower(), m.group(2), m.group(3)
        name = path.rstrip("/").split("/")[-1].replace("-", " ")
        # First occurrence wins; keeps stable order from the file.
        out.setdefault(cid, Category(id=cid, name=name, kind=kind, url=url))
    return list(out.values())


def category_id_set(links_file: Path = LINKS_FILE) -> set[str]:
    return {c.id for c in load_categories(links_file)}


def require_credentials() -> None:
    missing = [k for k, v in (("DIGIKEY_CLIENT_ID", CLIENT_ID),
                              ("DIGIKEY_CLIENT_SECRET", CLIENT_SECRET)) if not v]
    if missing:
        raise SystemExit(
            f"Missing required env vars: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill them in."
        )
