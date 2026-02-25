"""
Multi-platform marketplace search.

Poshmark and Mercari don't provide official public APIs, so we use
their internal web-search endpoints that their own frontends call.
These may break if they change their APIs — this is best-effort.

Facebook Marketplace uses Playwright (browser automation) and requires
a one-time login through the app.
"""

import asyncio
import logging
import httpx

log = logging.getLogger(__name__)


async def search_poshmark(query: str, limit: int = 20) -> list[dict]:
    """Search Poshmark listings via their internal search API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://poshmark.com/search",
                params={"query": query, "type": "listings", "src": "dir"},
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                return []

            # Poshmark returns HTML for browser requests; try JSON first
            try:
                data = resp.json()
                listings = data.get("data", [])[:limit]
            except Exception:
                return []

        items = []
        for listing in listings:
            price_str = listing.get("price_amount", {}).get("val") or listing.get("price", "")
            try:
                price = float(str(price_str).replace("$", "").replace(",", ""))
            except (ValueError, TypeError):
                price = None

            items.append({
                "title": listing.get("title", ""),
                "price": price,
                "currency": "USD",
                "condition": listing.get("condition", ""),
                "image_url": listing.get("picture_url", ""),
                "item_url": f"https://poshmark.com/listing/{listing.get('id', '')}",
                "source": "poshmark",
                "listing_type": "active",
            })
        return items
    except Exception:
        return []


async def search_mercari(query: str, limit: int = 20) -> list[dict]:
    """Search Mercari listings via their internal API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://www.mercari.com/v1/api",
                params={
                    "operationName": "searchFacetQuery",
                    "variables": f'{{"criteria":{{"keyword":"{query}","soldItemsOnly":false}},"itemsPerPage":{limit}}}',
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                return []

            try:
                data = resp.json()
            except Exception:
                return []

        search_results = (
            data.get("data", {})
            .get("search", {})
            .get("itemsList", [])
        )

        items = []
        for item in search_results[:limit]:
            try:
                price = float(item.get("price", 0)) / 100
            except (ValueError, TypeError):
                price = None

            items.append({
                "title": item.get("name", ""),
                "price": price,
                "currency": "USD",
                "condition": item.get("itemCondition", {}).get("name", ""),
                "image_url": item.get("thumbnails", [""])[0] if item.get("thumbnails") else "",
                "item_url": f"https://www.mercari.com/us/item/{item.get('id', '')}",
                "source": "mercari",
                "listing_type": "active",
            })
        return items
    except Exception:
        return []


async def search_facebook(query: str, limit: int = 30) -> list[dict]:
    """Search Facebook Marketplace (requires prior login via Playwright)."""
    from backend.services import settings as _settings
    if not _settings.get("fb_marketplace_enabled"):
        return []
    try:
        from backend.services.fb_scraper import search_fb_marketplace, is_fb_connected
        if not is_fb_connected():
            return []
        return await search_fb_marketplace(query, limit=limit)
    except ImportError:
        log.debug("Playwright not installed — skipping FB Marketplace")
        return []
    except Exception as e:
        log.warning("FB Marketplace search error: %s", e)
        return []


async def search_all_platforms(query: str) -> dict:
    """Search Poshmark, Mercari, and Facebook Marketplace in parallel."""
    posh_task = asyncio.create_task(search_poshmark(query))
    merc_task = asyncio.create_task(search_mercari(query))
    fb_task = asyncio.create_task(search_facebook(query))

    posh, merc, fb = await asyncio.gather(
        posh_task, merc_task, fb_task, return_exceptions=True,
    )

    return {
        "poshmark": posh if not isinstance(posh, Exception) else [],
        "mercari": merc if not isinstance(merc, Exception) else [],
        "facebook": fb if not isinstance(fb, Exception) else [],
    }
