"""Thin DigiKey Product Information API v4 client.

Handles app-only OAuth (client_credentials) with on-disk token caching, and the
v4 keyword search endpoint with pagination-friendly single-page calls plus retry
on 429/5xx. No anti-bot concerns here: api.digikey.com is a plain REST API, unlike
the Cloudflare-protected storefront.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

import config

_TIMEOUT = 30
_MAX_RETRIES = 4


class DigiKeyError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# OAuth                                                                        #
# --------------------------------------------------------------------------- #
def _load_cached_token() -> Optional[str]:
    try:
        data = json.loads(config.TOKEN_CACHE.read_text())
    except (FileNotFoundError, ValueError):
        return None
    # 60s safety margin before expiry.
    if data.get("expires_at", 0) - 60 > time.time():
        return data.get("access_token")
    return None


def _save_cached_token(access_token: str, expires_in: int) -> None:
    config.TOKEN_CACHE.write_text(json.dumps({
        "access_token": access_token,
        "expires_at": time.time() + int(expires_in),
    }))


def get_token(force: bool = False) -> str:
    """Return a valid bearer token, reusing the disk cache when possible."""
    if not force:
        cached = _load_cached_token()
        if cached:
            return cached

    config.require_credentials()
    resp = requests.post(
        config.TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": config.CLIENT_ID,
            "client_secret": config.CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise DigiKeyError(
            f"OAuth token request failed: HTTP {resp.status_code} {resp.text[:300]}"
        )
    payload = resp.json()
    token = payload["access_token"]
    _save_cached_token(token, payload.get("expires_in", 599))
    return token


# --------------------------------------------------------------------------- #
# Search                                                                       #
# --------------------------------------------------------------------------- #
def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_token()}",
        "X-DIGIKEY-Client-Id": config.CLIENT_ID,
        "X-DIGIKEY-Locale-Site": config.LOCALE_SITE,
        "X-DIGIKEY-Locale-Language": config.LOCALE_LANGUAGE,
        "X-DIGIKEY-Locale-Currency": config.LOCALE_CURRENCY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def keyword_search(
    *,
    keywords: str = "",
    manufacturer_id: Optional[int] = None,
    category_id: Optional[int] = None,
    param_filters: Optional[list[dict]] = None,
    limit: int = config.PAGE_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of v4 keyword search. Returns the parsed JSON body.

    param_filters: list of {"ParameterId": int, "FilterValues": [{"Id": valueId}]}
    used to subdivide a category that exceeds the 300-row window.
    """
    filters: dict[str, Any] = {}
    if manufacturer_id is not None:
        filters["ManufacturerFilter"] = [{"Id": int(manufacturer_id)}]
    if category_id is not None:
        filters["CategoryFilter"] = [{"Id": str(category_id)}]
    if param_filters:
        # ParameterFilterRequest requires its own (single-object) CategoryFilter.
        pfr: dict[str, Any] = {"ParameterFilters": param_filters}
        if category_id is not None:
            pfr["CategoryFilter"] = {"Id": str(category_id)}
        filters["ParameterFilterRequest"] = pfr

    body: dict[str, Any] = {
        "Keywords": keywords or "",
        "Limit": min(int(limit), config.PAGE_LIMIT),
        "Offset": int(offset),
    }
    if filters:
        body["FilterOptionsRequest"] = filters

    last_err = ""
    for attempt in range(_MAX_RETRIES):
        resp = requests.post(
            config.KEYWORD_SEARCH_URL, headers=_headers(), json=body, timeout=_TIMEOUT
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 401:
            # Token may have expired mid-run; force refresh once and retry.
            get_token(force=True)
            last_err = "401 unauthorized (refreshed token)"
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            # Respect Retry-After when present, else exponential backoff.
            wait = float(resp.headers.get("Retry-After", 2 ** attempt))
            last_err = f"HTTP {resp.status_code} {resp.text[:200]}"
            time.sleep(min(wait, 30))
            continue
        raise DigiKeyError(f"Search failed: HTTP {resp.status_code} {resp.text[:300]}")

    raise DigiKeyError(f"Search failed after {_MAX_RETRIES} retries: {last_err}")


def product_count(
    *,
    manufacturer_id: Optional[int] = None,
    category_id: Optional[int] = None,
    param_filters: Optional[list[dict]] = None,
) -> int:
    """Total matches for a filter combination (single lightweight call)."""
    page = keyword_search(
        manufacturer_id=manufacturer_id, category_id=category_id,
        param_filters=param_filters, limit=1, offset=0,
    )
    return int(page.get("ProductsCount", 0))


def parametric_filters(
    *,
    manufacturer_id: Optional[int] = None,
    category_id: Optional[int] = None,
    param_filters: Optional[list[dict]] = None,
) -> list[dict]:
    """Return the ParametricFilters block (used to subdivide big categories)."""
    page = keyword_search(
        manufacturer_id=manufacturer_id, category_id=category_id,
        param_filters=param_filters, limit=1, offset=0,
    )
    return (page.get("FilterOptions") or {}).get("ParametricFilters") or []


def iter_products(
    *,
    manufacturer_id: Optional[int] = None,
    category_id: Optional[int] = None,
    param_filters: Optional[list[dict]] = None,
    keywords: str = "",
    page_pause: float = 0.25,
):
    """Yield every product across all pages for the given filters (<= 300 window)."""
    offset = 0
    total = None
    while True:
        # DigiKey enforces Offset + Limit <= MAX_WINDOW; clamp the final page.
        limit = min(config.PAGE_LIMIT, config.MAX_WINDOW - offset)
        if limit <= 0:
            if total and total > config.MAX_WINDOW:
                print(f"  ! truncated: {total} matches exceed the {config.MAX_WINDOW}-row "
                      f"API window (cat={category_id}). Narrow the filter for full coverage.")
            break
        page = keyword_search(
            keywords=keywords,
            manufacturer_id=manufacturer_id,
            category_id=category_id,
            param_filters=param_filters,
            limit=limit,
            offset=offset,
        )
        products = page.get("Products") or []
        if total is None:
            total = page.get("ProductsCount", len(products))
        for p in products:
            yield p
        offset += len(products)
        if not products or offset >= (total or 0):
            break
        time.sleep(page_pause)


def resolve_manufacturer_id(name: str) -> Optional[int]:
    """Find the DigiKey manufacturer Id whose name matches `name` (case-insensitive)."""
    target = name.strip().lower()
    # First page of a name search is enough — the maker surfaces immediately.
    # Accept exact match first, else a startswith match (e.g. "Excelta" -> "Excelta Corporation").
    page = keyword_search(keywords=name, limit=config.PAGE_LIMIT, offset=0)
    fallback = None
    for p in page.get("Products") or []:
        mfr = p.get("Manufacturer") or {}
        mname = str(mfr.get("Name", "")).strip().lower()
        if not mfr.get("Id"):
            continue
        if mname == target:
            return int(mfr["Id"])
        if fallback is None and (mname.startswith(target) or target.startswith(mname)):
            fallback = int(mfr["Id"])
    return fallback
