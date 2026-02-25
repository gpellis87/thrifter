"""
Barcode/UPC lookup service.

Uses the free Open Food Facts + UPC ItemDB APIs to resolve barcode → product name,
then feeds that into the normal marketplace search pipeline.
"""

import httpx


async def lookup_upc(upc: str) -> dict | None:
    """
    Resolve a UPC/EAN barcode to product information.
    Tries multiple free databases in sequence.
    """
    upc = upc.strip()
    if not upc.isdigit() or len(upc) < 8:
        return None

    result = await _try_upcitemdb(upc)
    if result:
        return result

    result = await _try_open_food_facts(upc)
    if result:
        return result

    return {"title": upc, "search_query": upc, "brand": None, "category": None, "upc": upc}


async def _try_upcitemdb(upc: str) -> dict | None:
    """UPC Item DB — free tier, no key needed for small volume."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.upcitemdb.com/prod/trial/lookup",
                params={"upc": upc},
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()

        items = data.get("items", [])
        if not items:
            return None

        item = items[0]
        return {
            "title": item.get("title", ""),
            "search_query": item.get("title", upc),
            "brand": item.get("brand", None),
            "category": item.get("category", None),
            "upc": upc,
            "description": item.get("description", ""),
            "image_url": (item.get("images", []) or [""])[0],
        }
    except Exception:
        return None


async def _try_open_food_facts(upc: str) -> dict | None:
    """Open Food Facts — free, good for food/grocery items."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"https://world.openfoodfacts.org/api/v2/product/{upc}.json",
                headers={"User-Agent": "Thrifter/1.0"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()

        if data.get("status") != 1:
            return None

        product = data.get("product", {})
        name = product.get("product_name", "")
        brand = product.get("brands", "")

        if not name:
            return None

        return {
            "title": f"{brand} {name}".strip() if brand else name,
            "search_query": f"{brand} {name}".strip() if brand else name,
            "brand": brand or None,
            "category": product.get("categories", None),
            "upc": upc,
            "image_url": product.get("image_url", ""),
        }
    except Exception:
        return None
